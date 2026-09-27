"""Mutation runs and suggestions against the real database. The engine and suggester are real;
the sandbox boundaries are canned (the real ones: tests/sandbox/test_mutation_*.py)."""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.db.models import CodeAnalysis, MutantTestSuggestion, MutationRun
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.engine import MutationEngine
from testmind.mutation_engine.models import MutationReport
from testmind.mutation_engine.suggester import SuggestionResult, SuggestionStatus
from testmind.repositories.analysis_repository import AnalysisRepository
from testmind.sandbox.docker_sandbox import MutationJob, MutationResult
from testmind.sandbox.models import Outcome
from testmind.schemas.mutation import MutationRunRead
from testmind.schemas.project import ProjectCreate
from testmind.services.mutation_executor import (
    NO_ANALYSIS,
    NO_REPORT,
    MutationExecutor,
    SuggestionExecutor,
)
from testmind.services.mutation_service import MutationOptions, MutationService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import FailingDispatcher, RecordingDispatcher, ScriptedLLM
from tests.integration.test_generation_service import llm_configured  # noqa: F401  (fixture)
from tests.unit.mutation_engine.test_engine import APP, COVERAGE, MutationSandbox
from tests.unit.mutation_engine.test_suggester import TEST, Sandbox, driver, run
from tests.unit.test_engine.test_runner import Images, case, result
from tests.unit.test_engine.test_runner import Sandbox as CannedRun

KILL = "```python\nfrom app import double\n\ndef test_double():\n    assert double(2) == 4\n```"


async def analysed(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    (repos_root / "sample" / "app.py").write_text(APP)
    (repos_root / "sample" / "tests").mkdir()
    (repos_root / "sample" / "tests" / "test_app.py").write_text("def test_double(): pass\n")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="mut", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    return run.id


def mutation_executor(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    mutation: MutationSandbox | None = None,
) -> MutationExecutor:
    baseline = result(
        case("test_double", Outcome.PASSED, file="tests/test_app.py"),
        coverage_json=COVERAGE.replace("test_broken", "test_double"),
    )
    runner = TestSuiteRunner(CannedRun(baseline), Images())

    def engine(max_mutants: int) -> MutationEngine:
        return MutationEngine(
            runner, mutation or MutationSandbox(), max_mutants=max_mutants,
            mutant_timeout_cap=30, job_timeout_cap=600,
        )  # fmt: skip

    return MutationExecutor(session_factory, repos_root, engine)


async def stored_run(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> MutationRun:
    async with session_factory() as session:
        row = await session.get(MutationRun, job_id)
    assert row is not None
    return row


async def stored_suggestion(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> MutantTestSuggestion:
    async with session_factory() as session:
        row = await session.get(MutantTestSuggestion, job_id)
    assert row is not None
    return row


class Surviving(MutationSandbox):
    """Every mutant it is given survives."""

    def run_mutants(self, repo_root: Path, job: MutationJob) -> MutationResult:
        results = [{"id": m["id"], "status": "survived"} for m in job.mutants]
        return MutationResult(0, False, 1.0, json.dumps({"results": results}), "")


async def test_mutation_run_lifecycle(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = MutationService(session, dispatcher, settings)
        job = await service.create(run_id, MutationOptions(max_mutants=100))
        listed, total = await service.list_for_run(run_id, offset=0, limit=5)
    assert dispatcher.mutation_runs == [job.id]
    assert job.options == {"max_mutants": 100}
    assert (total, [j.id for j in listed]) == (1, [job.id])

    worker = mutation_executor(session_factory, repos_root)
    assert await worker.execute(job.id) is RunStatus.SUCCEEDED
    assert await worker.execute(job.id) is RunStatus.SUCCEEDED  # re-delivered: no rerun
    row = await stored_run(session_factory, job.id)
    report = MutationReport.model_validate(row.report)
    assert (report.totals.killed, report.totals.no_coverage, row.score) == (3, 3, 50.0)
    summary = MutationRunRead.from_row(row).summary
    assert summary is not None
    assert summary.totals.score == 50.0
    assert await worker.execute(uuid.uuid4()) is None


async def test_limits_and_errors(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)
    capped = settings.model_copy(update={"mutation_max_mutants": 5})
    async with session_factory() as session:
        job = await MutationService(session, RecordingDispatcher(), capped).create(
            run_id, MutationOptions(max_mutants=50)
        )
        assert job.options == {"max_mutants": 5}  # the server's limit wins
        service = MutationService(session, RecordingDispatcher(), settings)
        with pytest.raises(NotFoundError):
            await service.create(uuid.uuid4(), MutationOptions())
        with pytest.raises(NotFoundError):
            await service.list_for_run(uuid.uuid4(), offset=0, limit=1)
        with pytest.raises(NotFoundError, match="has no report"):
            await service.report(job.id)
        with pytest.raises(NotFoundError):
            await service.get_suggestion(uuid.uuid4())
        with pytest.raises(DispatchError):
            await MutationService(session, FailingDispatcher(), settings).create(
                run_id, MutationOptions()
            )
        queued_run = await RunService(session, RecordingDispatcher()).create(
            (await RunService(session, RecordingDispatcher()).get(run_id)).project_id
        )
        with pytest.raises(ConflictError, match="needs a succeeded run"):
            await service.create(queued_run.id, MutationOptions())


async def test_worker_failures(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)

    async def queued() -> uuid.UUID:
        async with session_factory() as session:
            return (await MutationService(session, RecordingDispatcher(), settings).create(
                run_id, MutationOptions()
            )).id  # fmt: skip

    crashed, unreadable, missing = await queued(), await queued(), await queued()
    with pytest.raises(RuntimeError):
        await mutation_executor(
            session_factory, repos_root, MutationSandbox(RuntimeError("bug"))
        ).execute(crashed)
    assert (await stored_run(session_factory, crashed)).error_message == UNEXPECTED_ERROR_MESSAGE

    shutil.rmtree(repos_root / "sample")  # the source went away after the request
    assert (
        await mutation_executor(session_factory, repos_root).execute(unreadable) is RunStatus.FAILED
    )
    assert "does not exist" in ((await stored_run(session_factory, unreadable)).error_message or "")

    async with session_factory() as session:
        code = await AnalysisRepository(session).get_code_for_run(run_id)
        assert code is not None
        await session.delete(await session.get(CodeAnalysis, code.id))
        await session.commit()
    assert await mutation_executor(session_factory, repos_root).execute(missing) is RunStatus.FAILED
    assert (await stored_run(session_factory, missing)).error_message == NO_ANALYSIS


@pytest.mark.usefixtures("llm_configured")
async def test_suggestions(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)
    async with session_factory() as session:
        job = await MutationService(session, RecordingDispatcher(), settings).create(
            run_id, MutationOptions()
        )
    await mutation_executor(session_factory, repos_root, Surviving()).execute(job.id)

    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = MutationService(session, dispatcher, settings)
        suggestion = await service.suggest(job.id)
        assert [s.id for s in await service.suggestions_for(job.id)] == [suggestion.id]
    assert dispatcher.mutant_suggestions == [suggestion.id]

    llm = ScriptedLLM([KILL, KILL, KILL])
    sandbox = Sandbox([run(Outcome.PASSED)] * 3, [driver("killed")] * 3)
    executor = SuggestionExecutor(
        session_factory, repos_root, provider_factory=lambda: llm, sandbox=sandbox,
        images=Images(), max_tokens=100, max_suggestions=2,
    )  # fmt: skip
    assert await executor.execute(suggestion.id) is RunStatus.SUCCEEDED
    row = await stored_suggestion(session_factory, suggestion.id)
    result = SuggestionResult.model_validate(row.result)
    assert [s.status for s in result.suggestions] == [SuggestionStatus.ACCEPTED] * 2
    assert (row.model, row.prompt_version, result.accepted) == ("scripted-model", "v1", 2)
    assert sandbox.jobs[0].image == "testmind-runner:abc"
    assert await executor.execute(suggestion.id) is RunStatus.SUCCEEDED
    assert await executor.execute(uuid.uuid4()) is None


async def test_suggestion_requests_are_checked(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await analysed(session_factory, repos_root)
    async with session_factory() as session:
        job = await MutationService(session, RecordingDispatcher(), settings).create(
            run_id, MutationOptions()
        )
    await mutation_executor(session_factory, repos_root).execute(job.id)  # everything killed
    async with session_factory() as session:
        service = MutationService(session, RecordingDispatcher(), settings)
        with pytest.raises(ConflictError, match="no surviving mutants"):
            await service.suggest(job.id)

    async with session_factory() as session:
        other = await MutationService(session, RecordingDispatcher(), settings).create(
            run_id, MutationOptions()
        )
    await mutation_executor(session_factory, repos_root, Surviving()).execute(other.id)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    async with session_factory() as session:
        with pytest.raises(LLMUnavailableError):
            await MutationService(session, RecordingDispatcher(), settings).suggest(other.id)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
        with pytest.raises(DispatchError):
            await MutationService(session, FailingDispatcher(), settings).suggest(other.id)
        [failed] = await MutationService(session, RecordingDispatcher(), settings).suggestions_for(
            other.id
        )
    assert failed.status is RunStatus.FAILED

    # A suggestion job whose mutation run lost its report cannot run.
    async with session_factory() as session:
        orphan = MutantTestSuggestion(
            mutation_run_id=job.id, status=RunStatus.QUEUED, provider="anthropic"
        )
        session.add(orphan)
        row = await session.get(MutationRun, job.id)
        assert row is not None
        row.report = None
        await session.commit()
    executor = SuggestionExecutor(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([TEST]),
        sandbox=Sandbox([], []), images=Images(), max_tokens=10, max_suggestions=1,
    )  # fmt: skip
    assert await executor.execute(orphan.id) is RunStatus.FAILED
    assert (await stored_suggestion(session_factory, orphan.id)).error_message == NO_REPORT


async def test_jobs_vanishing_or_failing_are_handled(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)

    async def surviving_run() -> uuid.UUID:
        async with session_factory() as session:
            job = await MutationService(session, RecordingDispatcher(), settings).create(
                run_id, MutationOptions()
            )
        await mutation_executor(session_factory, repos_root, Surviving()).execute(job.id)
        return job.id

    # A mutation run deleted while it ran: finishing it is a no-op.
    async with session_factory() as session:
        vanishing = await MutationService(session, RecordingDispatcher(), settings).create(
            run_id, MutationOptions()
        )

    class Deleting(MutationExecutor):
        async def _done(self, job_id, status, error, fields=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(MutationRun, job_id))
                await session.commit()
            return await super()._done(job_id, status, error, fields)

    worker = mutation_executor(session_factory, repos_root)
    deleting = Deleting(session_factory, repos_root, worker._engine_factory)
    assert await deleting.execute(vanishing.id) is RunStatus.SUCCEEDED
    async with session_factory() as session:
        assert await session.get(MutationRun, vanishing.id) is None

    # The model provider failing, or crashing, fails the suggestion job.
    mutation_run_id = await surviving_run()

    def failing_provider() -> ScriptedLLM:
        raise LLMError("provider down")

    def crashing_provider() -> ScriptedLLM:
        raise RuntimeError("bug")

    cases = (
        (failing_provider, False, "provider down"),
        (crashing_provider, True, UNEXPECTED_ERROR_MESSAGE),
    )
    for factory, crashes, message in cases:
        async with session_factory() as session:
            job = MutantTestSuggestion(
                mutation_run_id=mutation_run_id, status=RunStatus.QUEUED, provider="anthropic"
            )
            session.add(job)
            await session.commit()
        executor = SuggestionExecutor(
            session_factory, repos_root, provider_factory=factory, sandbox=Sandbox([], []),
            images=Images(), max_tokens=10, max_suggestions=1,
        )  # fmt: skip
        if crashes:
            with pytest.raises(RuntimeError):
                await executor.execute(job.id)
        else:
            assert await executor.execute(job.id) is RunStatus.FAILED
        assert (await stored_suggestion(session_factory, job.id)).error_message == message


async def test_dispatch_failure_after_concurrent_delete(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_mutation_run(self, mutation_run_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(MutationRun, mutation_run_id))
                await other.commit()
            await super().dispatch_mutation_run(mutation_run_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await MutationService(session, DeleteThenFail(), settings).create(
                run_id, MutationOptions()
            )
