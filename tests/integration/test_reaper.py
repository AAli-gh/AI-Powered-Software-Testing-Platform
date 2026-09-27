import asyncio
import importlib
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import get_settings
from testmind.db.models import (
    AnalysisRun,
    ApiTestSuggestion,
    FailureAnalysis,
    MutantTestSuggestion,
    MutationRun,
    Project,
    Regression,
    TestExecution,
    TestGeneration,
)
from testmind.domain.enums import RunStatus, SourceType
from testmind.services.reaper import ORPHANED_MESSAGE, reap_orphaned_jobs
from tests.conftest import TEST_DATABASE_URL, TEST_REDIS_URL


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, object]:
    old = datetime.now(UTC) - timedelta(hours=2)
    recent = datetime.now(UTC) - timedelta(minutes=1)
    async with session_factory() as session:
        project = Project(name="p", source_type=SourceType.LOCAL, source_location="sample")
        session.add(project)
        await session.flush()
        stuck_run = AnalysisRun(project_id=project.id, status=RunStatus.RUNNING, started_at=old)
        live_run = AnalysisRun(project_id=project.id, status=RunStatus.RUNNING, started_at=recent)
        done_run = AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED, started_at=old)
        session.add_all([stuck_run, live_run, done_run])
        await session.flush()
        generation = TestGeneration(
            run_id=done_run.id,
            status=RunStatus.RUNNING,
            started_at=old,
            options={},
            provider="anthropic",
        )
        execution = TestExecution(run_id=done_run.id, status=RunStatus.RUNNING, started_at=old)
        session.add_all([generation, execution])
        await session.flush()
        analysis = FailureAnalysis(
            execution_id=execution.id, status=RunStatus.RUNNING, started_at=old, provider="x"
        )
        regression = Regression(
            project_id=project.id, status=RunStatus.RUNNING, started_at=old, mode="full",
            base_ref="a", head_ref="b", base_commit="a" * 40, head_commit="b" * 40,
        )  # fmt: skip
        mutation = MutationRun(
            run_id=done_run.id, status=RunStatus.RUNNING, started_at=old, options={}
        )
        session.add_all([analysis, regression, mutation])
        await session.flush()
        suggestion = MutantTestSuggestion(
            mutation_run_id=mutation.id, status=RunStatus.RUNNING, started_at=old, provider="x"
        )
        api_suggestion = ApiTestSuggestion(
            execution_id=execution.id, status=RunStatus.RUNNING, started_at=old, provider="x"
        )
        session.add_all([suggestion, api_suggestion])
        await session.commit()
        return {
            "stuck": stuck_run.id,
            "live": live_run.id,
            "done": done_run.id,
            "gen": generation.id,
            "exe": execution.id,
            "fa": analysis.id,
            "reg": regression.id,
            "mut": mutation.id,
            "sug": suggestion.id,
            "api": api_suggestion.id,
        }


async def test_only_long_running_jobs_are_reaped(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    ids = await seed(session_factory)
    assert await reap_orphaned_jobs(session_factory, older_than=timedelta(hours=1)) == 8

    async with session_factory() as session:
        stuck = await session.get(AnalysisRun, ids["stuck"])
        live = await session.get(AnalysisRun, ids["live"])
        done = await session.get(AnalysisRun, ids["done"])
        generation = await session.get(TestGeneration, ids["gen"])
        execution = await session.get(TestExecution, ids["exe"])
        analysis = await session.get(FailureAnalysis, ids["fa"])
        regression = await session.get(Regression, ids["reg"])
        mutation = await session.get(MutationRun, ids["mut"])
        suggestion = await session.get(MutantTestSuggestion, ids["sug"])
        api_suggestion = await session.get(ApiTestSuggestion, ids["api"])
    assert stuck is not None
    assert stuck.status is RunStatus.FAILED
    assert stuck.error_message == ORPHANED_MESSAGE
    assert stuck.finished_at is not None
    assert live is not None
    assert live.status is RunStatus.RUNNING
    assert done is not None
    assert done.status is RunStatus.SUCCEEDED
    assert generation is not None
    assert generation.status is RunStatus.FAILED
    assert execution is not None
    assert execution.status is RunStatus.FAILED
    assert analysis is not None
    assert analysis.status is RunStatus.FAILED
    assert regression is not None
    assert regression.status is RunStatus.FAILED
    assert mutation is not None
    assert mutation.status is RunStatus.FAILED
    assert suggestion is not None
    assert suggestion.status is RunStatus.FAILED
    assert api_suggestion is not None
    assert api_suggestion.status is RunStatus.FAILED

    assert await reap_orphaned_jobs(session_factory, older_than=timedelta(hours=1)) == 0


async def test_worker_startup_hook_reaps(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    await seed(session_factory)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTMIND_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("TESTMIND_REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("TESTMIND_REPOS_ROOT", str(tmp_path))
    monkeypatch.setenv("TESTMIND_CELERY_TASK_TIME_LIMIT_SECONDS", "3600")
    get_settings.cache_clear()
    sys.modules.pop("testmind.workers.main", None)
    try:
        main = importlib.import_module("testmind.workers.main")  # fresh, with the test env
        assert await main.reap_on_startup() == 8
    finally:
        sys.modules.pop("testmind.workers.main", None)
        get_settings.cache_clear()
        assert "TESTMIND_DATABASE_URL" in os.environ


async def test_worker_ready_signal_handler(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ids = await seed(session_factory)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TESTMIND_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("TESTMIND_REDIS_URL", TEST_REDIS_URL)
    monkeypatch.setenv("TESTMIND_REPOS_ROOT", str(tmp_path))
    get_settings.cache_clear()
    sys.modules.pop("testmind.workers.main", None)
    try:
        main = importlib.import_module("testmind.workers.main")
        # Celery calls the handler synchronously from its own (non-async) thread.
        await asyncio.to_thread(main._reap_orphaned_jobs)
    finally:
        sys.modules.pop("testmind.workers.main", None)
        get_settings.cache_clear()
    async with session_factory() as session:
        stuck = await session.get(AnalysisRun, ids["stuck"])
    assert stuck is not None
    assert stuck.status is RunStatus.FAILED
