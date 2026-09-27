"""End-to-end through the real queue: API service → Celery (Redis) → worker → PostgreSQL."""

import asyncio
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions
from testmind.core.config import Settings
from testmind.db.models import (
    AnalysisRun,
    ApiTestSuggestion,
    FailureAnalysis,
    MutantTestSuggestion,
    MutationRun,
    TestGeneration,
)
from testmind.domain.enums import RunStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.schemas.project import ProjectCreate
from testmind.services.project_service import ProjectService
from testmind.services.run_service import RunService
from testmind.workers.celery_app import create_celery_app
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.integration.test_api_testing_service import PROJECT_CALL, executed
from tests.integration.test_api_testing_service import analysed as api_analysed
from tests.integration.test_failure_analysis import failed_execution
from tests.integration.test_mutation_service import analysed
from tests.unit.mutation_engine.test_suggester import report, survivor

WAIT_SECONDS = 20


@pytest.fixture
def celery_app(settings: Settings) -> Celery:
    app = create_celery_app(settings)
    app.control.purge()  # drop messages left over from other tests
    return app


@pytest.fixture
def worker(celery_app: Celery) -> Iterator[None]:
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        yield


async def wait_for_terminal(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> AnalysisRun:
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                run = await session.get(AnalysisRun, run_id)
                if run is not None and run.status.is_terminal:
                    return run
            await asyncio.sleep(0.1)


async def queue_run(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> uuid.UUID:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
        run = await RunService(session, CeleryTaskDispatcher(celery_app)).create(project.id)
        return run.id


@pytest.mark.usefixtures("worker")
async def test_run_succeeds_through_the_real_queue(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    run_id = await queue_run(session_factory, celery_app, repos_root)

    run = await wait_for_terminal(session_factory, run_id)

    assert run.status is RunStatus.SUCCEEDED
    assert run.started_at is not None
    assert run.finished_at is not None
    async with session_factory() as session:
        assert await AnalysisRepository(session).get_for_run(run_id) is not None
        assert await AnalysisRepository(session).get_code_for_run(run_id) is not None


@pytest.mark.usefixtures("worker")
async def test_run_fails_when_source_disappears(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
    shutil.rmtree(repos_root / "sample")
    async with session_factory() as session:
        run = await RunService(session, CeleryTaskDispatcher(celery_app)).create(project.id)

    finished = await wait_for_terminal(session_factory, run.id)

    assert finished.status is RunStatus.FAILED
    assert finished.error_message is not None


async def test_run_cancelled_before_pickup_is_skipped_by_worker(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    run_id = await queue_run(session_factory, celery_app, repos_root)  # no worker running yet
    async with session_factory() as session:
        await RunService(session, CeleryTaskDispatcher(celery_app)).cancel(run_id)

    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        await asyncio.sleep(2)  # give the worker time to consume the queued message

    async with session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        assert run.status is RunStatus.CANCELLED
        assert run.started_at is None


async def test_generation_without_llm_fails_cleanly_in_the_real_worker(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    repos_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No doubles at all: the real worker, the real provider factory, no API key."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    run_id = await queue_run(session_factory, celery_app, repos_root)
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        await wait_for_terminal(session_factory, run_id)
        async with session_factory() as session:
            generation = TestGeneration(
                run_id=run_id,
                status=RunStatus.QUEUED,
                options=GenerationOptions().model_dump(mode="json"),
                provider="anthropic",
            )
            session.add(generation)
            await session.commit()
        await CeleryTaskDispatcher(celery_app).dispatch_test_generation(generation.id)

        async with asyncio.timeout(WAIT_SECONDS):
            while True:
                async with session_factory() as session:
                    stored = await session.get(TestGeneration, generation.id)
                    if stored is not None and stored.status.is_terminal:
                        break
                await asyncio.sleep(0.1)

    assert stored.status is RunStatus.FAILED
    assert stored.error_message is not None
    assert stored.error_message.startswith("No LLM provider is configured")


@pytest.mark.usefixtures("worker")
async def test_failure_analysis_without_llm_fails_cleanly_in_the_real_worker(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    repos_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real worker and provider factory, no API key: the job fails with the reason."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    execution_id = await failed_execution(session_factory, repos_root)
    async with session_factory() as session:
        analysis = FailureAnalysis(
            execution_id=execution_id, status=RunStatus.QUEUED, provider="anthropic"
        )
        session.add(analysis)
        await session.commit()
    await CeleryTaskDispatcher(celery_app).dispatch_failure_analysis(analysis.id)

    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                stored = await session.get(FailureAnalysis, analysis.id)
                if stored is not None and stored.status.is_terminal:
                    break
            await asyncio.sleep(0.1)

    assert stored.status is RunStatus.FAILED
    assert stored.error_message is not None
    assert stored.error_message.startswith("No LLM provider is configured")


@pytest.mark.usefixtures("worker")
async def test_mutant_suggestions_without_llm_fail_cleanly_in_the_real_worker(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    repos_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real worker and provider factory, no API key: the job fails before any image build."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    run_id = await analysed(session_factory, repos_root)
    async with session_factory() as session:
        mutation_run = MutationRun(
            run_id=run_id,
            status=RunStatus.SUCCEEDED,
            options={"max_mutants": 10},
            report=report(survivor()).model_dump(mode="json"),
        )
        session.add(mutation_run)
        await session.flush()
        suggestion = MutantTestSuggestion(
            mutation_run_id=mutation_run.id, status=RunStatus.QUEUED, provider="anthropic"
        )
        session.add(suggestion)
        await session.commit()
    await CeleryTaskDispatcher(celery_app).dispatch_mutant_suggestions(suggestion.id)

    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                stored = await session.get(MutantTestSuggestion, suggestion.id)
                if stored is not None and stored.status.is_terminal:
                    break
            await asyncio.sleep(0.1)

    assert stored.status is RunStatus.FAILED
    assert (stored.error_message or "").startswith("No LLM provider is configured")


@pytest.mark.usefixtures("worker")
async def test_api_suggestions_without_llm_fail_cleanly_in_the_real_worker(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    repos_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real worker and provider factory, no API key: the job fails before any image build."""
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    run_id = await api_analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)
    async with session_factory() as session:
        suggestion = ApiTestSuggestion(
            execution_id=execution_id, status=RunStatus.QUEUED, provider="anthropic"
        )
        session.add(suggestion)
        await session.commit()
    await CeleryTaskDispatcher(celery_app).dispatch_api_suggestions(suggestion.id)

    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                stored = await session.get(ApiTestSuggestion, suggestion.id)
                if stored is not None and stored.status.is_terminal:
                    break
            await asyncio.sleep(0.1)

    assert stored.status is RunStatus.FAILED
    assert (stored.error_message or "").startswith("No LLM provider is configured")
