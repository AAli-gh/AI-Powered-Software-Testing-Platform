"""Failure analysis against the real database: the stored deterministic report, requesting an
AI analysis, and the worker side. The sandbox and the LLM are the doubles here; the real
sandbox path is in tests/sandbox/test_failure_analysis_end_to_end.py."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMNotConfiguredError, LLMProvider
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.db.models import FailureAnalysis, TestExecution
from testmind.domain.enums import RunStatus
from testmind.failure_engine.models import AnalysisStatus, Blame, FailureAnalysisResult
from testmind.sandbox.models import Outcome
from testmind.schemas.project import ProjectCreate
from testmind.schemas.test_execution import TestExecutionRead
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.failure_analysis_executor import NO_FAILURE_REPORT, FailureAnalysisExecutor
from testmind.services.failure_analysis_service import FailureAnalysisService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import FailingDispatcher, RecordingDispatcher, ScriptedLLM
from tests.integration.test_generation_service import (
    analysed_run,
    llm_configured,  # noqa: F401  (fixture)
)
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

MAIN = "def ratio(a, b):\n    return a / b\n"
TEST = "from main import ratio\n\n\ndef test_ratio():\n    assert ratio(1, 0) == 0\n"
CAPTURE = json.dumps(
    {
        "version": 1,
        "rootdir": "/workspace/src",
        "failures": [
            {
                "phase": "call",
                "path": "/workspace/src/test_main.py",
                "name": "test_ratio",
                "line": 4,
                "exception": {"type": "ZeroDivisionError", "message": "division by zero"},
                "frames": [
                    {"path": "/workspace/src/test_main.py", "line": 5, "function": "test_ratio"},
                    {"path": "/workspace/src/main.py", "line": 2, "function": "ratio"},
                ],
                "locals": {"a": "1", "b": "0"},
            }
        ],
    }
)
# test_ratio executed ratio's body (per-test coverage contexts, for fault localisation)
COVERAGE = json.dumps(
    {
        "meta": {"version": "7.16.1", "branch_coverage": True},
        "files": {
            "main.py": {
                "executed_lines": [1, 2],
                "missing_lines": [],
                "contexts": {"2": ["test_main.py::test_ratio|run"]},
            }
        },
    }
)
REPLY = (
    "```json\n"
    + json.dumps(
        {
            "blame": "code",
            "root_cause": "ratio() divides by b without checking for zero.",
            "evidence": [{"path": "main.py", "line": 2, "explanation": "a / b with b = 0"}],
            "suggested_fix": "Guard against b == 0.",
            "confidence": 0.8,
        }
    )
    + "\n```"
)


async def failed_execution(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    *,
    passing: bool = False,
) -> uuid.UUID:
    (repos_root / "sample" / "main.py").write_text(MAIN)
    (repos_root / "sample" / "test_main.py").write_text(TEST)
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="failing", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    run_id = run.id
    async with session_factory() as session:
        execution = await ExecutionService(session, RecordingDispatcher()).create(run_id, None)
    canned = (
        result(case("test_ratio", Outcome.PASSED), failures_json=json.dumps(
            {"version": 1, "rootdir": "/workspace/src", "failures": []}
        ))
        if passing
        else result(
            case("test_ratio", Outcome.FAILED, file="test_main.py"), exit_code=1,
            failures_json=CAPTURE, coverage_json=COVERAGE,
        )
    )  # fmt: skip
    runner = TestSuiteRunner(Sandbox(canned), Images())
    await ExecutionExecutor(session_factory, repos_root, runner).execute(execution.id)
    return execution.id


def worker(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    provider: LLMProvider,
) -> FailureAnalysisExecutor:
    return FailureAnalysisExecutor(
        session_factory, repos_root, provider_factory=lambda: provider, max_tokens=500,
        max_groups=5,
    )  # fmt: skip


async def test_deterministic_report_is_stored_with_the_execution(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root)
    async with session_factory() as session:
        report = await FailureAnalysisService(session, RecordingDispatcher(), settings).report(
            execution_id
        )
        row = await session.get(TestExecution, execution_id)
    assert row is not None
    [group] = report.groups
    assert (group.exception_type, group.locals) == ("ZeroDivisionError", {"a": "1", "b": "0"})
    assert group.function is not None
    assert group.function.qualname == "ratio"
    # fault localisation: the traceback's production frame, first and with high confidence
    assert group.localization is not None
    assert [(s.qualname, s.rank, s.in_traceback) for s in group.localization.suspects] == [
        ("ratio", 1, True)
    ]
    assert group.localization.confidence == "high"
    assert row.spectrum == {
        "tests": ["test_main.py::test_ratio"],
        "functions": {"main.py::ratio": [1, [0]]},
    }
    summary = TestExecutionRead.from_row(row).summary
    assert summary is not None
    assert summary.failure_groups == 1


async def test_report_errors(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        queued = await ExecutionService(session, RecordingDispatcher()).create(run_id, None)
        service = FailureAnalysisService(session, RecordingDispatcher(), settings)
        with pytest.raises(NotFoundError, match=r"no failure report \(status: queued"):
            await service.report(queued.id)
        with pytest.raises(NotFoundError, match="not found"):
            await service.report(uuid.uuid4())
        with pytest.raises(NotFoundError, match="not found"):
            await service.get(uuid.uuid4())
        with pytest.raises(NotFoundError, match="not found"):
            await service.list_for_execution(uuid.uuid4(), offset=0, limit=5)


@pytest.mark.usefixtures("llm_configured")
async def test_nothing_to_analyse(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root, passing=True)
    async with session_factory() as session:
        service = FailureAnalysisService(session, RecordingDispatcher(), settings)
        with pytest.raises(ConflictError, match="no failure groups"):
            await service.create(execution_id)


async def test_ai_analysis_needs_an_llm(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    execution_id = await failed_execution(session_factory, repos_root)
    async with session_factory() as session:
        service = FailureAnalysisService(session, RecordingDispatcher(), settings)
        with pytest.raises(LLMUnavailableError, match="deterministic analysis is available"):
            await service.create(execution_id)


@pytest.mark.usefixtures("llm_configured")
async def test_dispatch_failure(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root)
    async with session_factory() as session:
        service = FailureAnalysisService(session, FailingDispatcher(), settings)
        with pytest.raises(DispatchError):
            await service.create(execution_id)
        [analysis], total = await service.list_for_execution(execution_id, offset=0, limit=5)
    assert total == 1
    assert analysis.status is RunStatus.FAILED


@pytest.mark.usefixtures("llm_configured")
async def test_requested_analysis_runs_in_the_worker(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        analysis = await FailureAnalysisService(session, dispatcher, settings).create(execution_id)
    assert dispatcher.failure_analyses == [analysis.id]
    llm = ScriptedLLM([REPLY])

    assert (
        await worker(session_factory, repos_root, llm).execute(analysis.id) is RunStatus.SUCCEEDED
    )
    # A re-delivered message does not analyse twice.
    assert (
        await worker(session_factory, repos_root, llm).execute(analysis.id) is RunStatus.SUCCEEDED
    )
    assert len(llm.requests) == 1

    async with session_factory() as session:
        row = await session.get(FailureAnalysis, analysis.id)
    assert row is not None
    assert (row.model, row.prompt_version) == ("scripted-model", "v1")
    [hypothesis] = FailureAnalysisResult.model_validate(row.result).hypotheses
    assert (hypothesis.status, hypothesis.blame, hypothesis.confidence) == (
        AnalysisStatus.ANALYSED,
        Blame.CODE,
        0.8,
    )
    assert [e.verified for e in hypothesis.evidence] == [True]
    assert "    2 |     return a / b" in llm.requests[0].prompt  # read from the repository


@pytest.mark.usefixtures("llm_configured")
async def test_worker_failures(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root)

    async def queued() -> uuid.UUID:
        async with session_factory() as session:
            created = await FailureAnalysisService(session, RecordingDispatcher(), settings).create(
                execution_id
            )
            return created.id

    async def error_of(analysis_id: uuid.UUID) -> str | None:
        async with session_factory() as session:
            row = await session.get(FailureAnalysis, analysis_id)
            assert row is not None
            return row.error_message

    class NotConfigured:
        name = "none"

        def complete(self, request: object) -> object:
            raise AssertionError("never called")

    not_configured = await queued()
    executor = FailureAnalysisExecutor(
        session_factory, repos_root, max_tokens=10, max_groups=1,
        provider_factory=lambda: (_ for _ in ()).throw(LLMNotConfiguredError("no key")),
    )  # fmt: skip
    assert await executor.execute(not_configured) is RunStatus.FAILED
    assert await error_of(not_configured) == "no key"

    class Crashing:
        name = "crash"

        def complete(self, request: object) -> object:
            raise RuntimeError("bug")

    crashed = await queued()
    with pytest.raises(RuntimeError):
        await worker(session_factory, repos_root, Crashing()).execute(crashed)  # type: ignore[arg-type]
    assert await error_of(crashed) == UNEXPECTED_ERROR_MESSAGE

    lost_report = await queued()
    async with session_factory() as session:
        row = await session.get(TestExecution, execution_id)
        assert row is not None
        row.failures = None
        await session.commit()
    assert (
        await worker(session_factory, repos_root, NotConfigured()).execute(  # type: ignore[arg-type]
            lost_report
        )
        is RunStatus.FAILED
    )
    assert await error_of(lost_report) == NO_FAILURE_REPORT

    assert await worker(session_factory, repos_root, NotConfigured()).execute(uuid.uuid4()) is None  # type: ignore[arg-type]


@pytest.mark.usefixtures("llm_configured")
async def test_concurrent_deletes_are_tolerated(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    execution_id = await failed_execution(session_factory, repos_root)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_failure_analysis(self, analysis_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(FailureAnalysis, analysis_id))
                await other.commit()
            await super().dispatch_failure_analysis(analysis_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await FailureAnalysisService(session, DeleteThenFail(), settings).create(execution_id)

    async with session_factory() as session:
        analysis = await FailureAnalysisService(session, RecordingDispatcher(), settings).create(
            execution_id
        )

    class Deleting(FailureAnalysisExecutor):
        async def _finish(self, analysis_id, status, error_message, result=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(FailureAnalysis, analysis_id))
                await session.commit()
            return await super()._finish(analysis_id, status, error_message, result)

    deleting = Deleting(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([REPLY]),
        max_tokens=10, max_groups=1,
    )  # fmt: skip
    assert await deleting.execute(analysis.id) is RunStatus.SUCCEEDED
