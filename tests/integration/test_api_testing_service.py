"""API coverage, probes and AI API test suggestions against the real database. The coverage
builder, probes and suggester are real; the sandbox answers are canned (the real ones:
tests/sandbox/test_api_testing.py)."""

import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.llm import LLMError
from testmind.api_engine.models import ProbeVerdict
from testmind.api_engine.probes import PROBE_FILE
from testmind.api_engine.suggester import ApiSuggestionResult
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, DispatchError, LLMUnavailableError, NotFoundError
from testmind.db.models import ApiTestSuggestion, TestExecution
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.suggester import SuggestionStatus
from testmind.sandbox.models import Outcome
from testmind.schemas.project import ProjectCreate
from testmind.schemas.test_execution import TestExecutionRead
from testmind.services.api_suggestion_executor import NO_API_COVERAGE, ApiSuggestionExecutor
from testmind.services.api_testing_service import ApiTestingService
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import FailingDispatcher, RecordingDispatcher, ScriptedLLM
from tests.integration.test_generation_service import llm_configured  # noqa: F401  (fixture)
from tests.unit.api_engine.test_suggester import Sandbox as SuggestionSandbox
from tests.unit.api_engine.test_suggester import request_record
from tests.unit.api_engine.test_suggester import run as suggestion_run
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

APP = """from flask import Flask

app = Flask(__name__)


@app.get("/items")
def items():
    return []


@app.delete("/items/<int:item_id>")
def remove(item_id):
    return "", 204
"""
TEST = "```python\nfrom shop import app\n\ndef test_x():\n    assert app.test_client()\n```"


async def analysed(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    (repos_root / "sample" / "shop.py").write_text(APP)
    (repos_root / "sample" / "tests").mkdir()
    (repos_root / "sample" / "tests" / "test_shop.py").write_text("def test_items(): pass\n")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="api", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    return run.id


def api_capture(*calls: dict[str, object]) -> str:
    return json.dumps({"version": 1, "rootdir": "/workspace/src", "calls": list(calls), "apps": []})


PROJECT_CALL = request_record(
    "GET", "/items", 200, test="test_items", file="/workspace/src/tests/test_shop.py"
)


async def executed(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: uuid.UUID,
    *calls: dict[str, object],
    api_probes: bool = False,
) -> tuple[uuid.UUID, Sandbox]:
    async with session_factory() as session:
        execution = await ExecutionService(session, RecordingDispatcher()).create(
            run_id, None, api_probes=api_probes
        )
    sandbox = Sandbox(result(case("test_items", Outcome.PASSED), api_json=api_capture(*calls)))
    executor = ExecutionExecutor(session_factory, repos_root, TestSuiteRunner(sandbox, Images()))
    await executor.execute(execution.id)
    return execution.id, sandbox


async def stored(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> ApiTestSuggestion:
    async with session_factory() as session:
        row = await session.get(ApiTestSuggestion, job_id)
    assert row is not None
    return row


async def test_api_coverage_is_stored_with_the_execution(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed(session_factory, repos_root)
    probe = {
        **PROJECT_CALL, "test_path": f"/workspace/tests/{PROBE_FILE}",
        "test_name": "test_probe_001_unknown_path", "route": None, "status": 404,
    }  # fmt: skip
    execution_id, sandbox = await executed(
        session_factory, repos_root, run_id, PROJECT_CALL, probe, api_probes=True
    )
    assert PROBE_FILE in sandbox.jobs[0].test_files
    async with session_factory() as session:
        row = await session.get(TestExecution, execution_id)
        assert row is not None
        report = await ExecutionService(session, RecordingDispatcher()).api_coverage(execution_id)
    assert (row.api_probes, row.api_coverage_percent) == (True, 50.0)
    assert [(e.method, e.path, e.tested) for e in report.endpoints] == [
        ("GET", "/items", True),
        ("DELETE", "/items/{item_id}", False),
    ]
    assert report.probes[0].verdict is ProbeVerdict.PASSED
    summary = TestExecutionRead.from_row(row)
    assert summary.api_probes
    assert summary.summary is not None
    assert summary.summary.api_coverage_percent == 50.0


async def test_api_coverage_missing(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed(session_factory, repos_root)
    async with session_factory() as session:
        service = ExecutionService(session, RecordingDispatcher())
        queued = await service.create(run_id, None)
        with pytest.raises(NotFoundError, match="has not finished"):
            await service.api_coverage(queued.id)
    (repos_root / "sample" / "shop.py").write_text("x = 1\n")
    other_run = await analysed_again(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, other_run)
    async with session_factory() as session:
        with pytest.raises(NotFoundError, match="has no HTTP endpoints"):
            await ExecutionService(session, RecordingDispatcher()).api_coverage(execution_id)


async def analysed_again(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    async with session_factory() as session:
        [project], _ = await ProjectService(session, repos_root).list(offset=0, limit=1)
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    return run.id


@pytest.mark.usefixtures("llm_configured")
async def test_suggestion_lifecycle(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        service = ApiTestingService(session, dispatcher, settings)
        job = await service.suggest(execution_id)
        assert [s.id for s in await service.suggestions_for(execution_id)] == [job.id]
        assert (await service.get_suggestion(job.id)).status is RunStatus.QUEUED
    assert dispatcher.api_suggestions == [job.id]

    llm = ScriptedLLM([TEST])
    sandbox = SuggestionSandbox(
        suggestion_run(request_record("DELETE", "/items/<int:item_id>", 204))
    )
    executor = ApiSuggestionExecutor(
        session_factory, repos_root, provider_factory=lambda: llm, sandbox=sandbox,
        images=Images(), max_tokens=100, max_endpoints=3,
    )  # fmt: skip
    assert await executor.execute(job.id) is RunStatus.SUCCEEDED
    assert await executor.execute(job.id) is RunStatus.SUCCEEDED  # re-delivered: not rerun
    assert await executor.execute(uuid.uuid4()) is None
    row = await stored(session_factory, job.id)
    outcome = ApiSuggestionResult.model_validate(row.result)
    assert [(s.method, s.status) for s in outcome.suggestions] == [
        ("DELETE", SuggestionStatus.ACCEPTED)
    ]
    assert (row.model, row.prompt_version) == ("scripted-model", "v1")
    assert sandbox.jobs[0].image == "testmind-runner:abc"


async def test_requests_are_checked(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = await analysed(session_factory, repos_root)
    everything = request_record(
        "DELETE", "/items/<int:item_id>", 204, test="test_items",
        file="/workspace/src/tests/test_shop.py",
    )  # fmt: skip
    missing = {**everything, "status": 404}  # the parameter's error path is tested too
    complete, _ = await executed(
        session_factory, repos_root, run_id, PROJECT_CALL, everything, missing
    )
    gaps, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    async with session_factory() as session:
        service = ApiTestingService(session, RecordingDispatcher(), settings)
        with pytest.raises(ConflictError, match="no endpoint gaps"):
            await service.suggest(complete)
        with pytest.raises(LLMUnavailableError):
            await service.suggest(gaps)
        with pytest.raises(NotFoundError):
            await service.get_suggestion(uuid.uuid4())
        with pytest.raises(NotFoundError):
            await service.suggestions_for(uuid.uuid4())
        monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
        with pytest.raises(DispatchError):
            await ApiTestingService(session, FailingDispatcher(), settings).suggest(gaps)
        [failed] = await service.suggestions_for(gaps)
    assert failed.status is RunStatus.FAILED


async def test_dispatch_failure_after_concurrent_delete(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
    run_id = await analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_api_suggestions(self, suggestion_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(ApiTestSuggestion, suggestion_id))
                await other.commit()
            await super().dispatch_api_suggestions(suggestion_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await ApiTestingService(session, DeleteThenFail(), settings).suggest(execution_id)


async def test_worker_failures(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)

    async def queued(execution: uuid.UUID = execution_id) -> uuid.UUID:
        async with session_factory() as session:
            job = ApiTestSuggestion(
                execution_id=execution, status=RunStatus.QUEUED, provider="anthropic"
            )
            session.add(job)
            await session.commit()
            return job.id

    def executor(factory: object) -> ApiSuggestionExecutor:
        return ApiSuggestionExecutor(
            session_factory, repos_root, provider_factory=factory,  # type: ignore[arg-type]
            sandbox=SuggestionSandbox(), images=Images(), max_tokens=10, max_endpoints=1,
        )  # fmt: skip

    def failing() -> ScriptedLLM:
        raise LLMError("provider down")

    def crashing() -> ScriptedLLM:
        raise RuntimeError("bug")

    job = await queued()
    assert await executor(failing).execute(job) is RunStatus.FAILED
    assert (await stored(session_factory, job)).error_message == "provider down"

    job = await queued()
    with pytest.raises(RuntimeError):
        await executor(crashing).execute(job)
    assert (await stored(session_factory, job)).error_message == UNEXPECTED_ERROR_MESSAGE

    # An execution without API coverage has nothing to work from.
    async with session_factory() as session:
        bare = await ExecutionService(session, RecordingDispatcher()).create(run_id, None)
    job = await queued(bare.id)
    assert await executor(failing).execute(job) is RunStatus.FAILED
    assert (await stored(session_factory, job)).error_message == NO_API_COVERAGE

    # The source went away after the request.
    job = await queued()
    shutil.rmtree(repos_root / "sample")
    assert await executor(failing).execute(job) is RunStatus.FAILED
    assert "does not exist" in ((await stored(session_factory, job)).error_message or "")


async def test_a_job_deleted_while_it_ran(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)
    async with session_factory() as session:
        job = ApiTestSuggestion(
            execution_id=execution_id, status=RunStatus.QUEUED, provider="anthropic"
        )
        session.add(job)
        await session.commit()

    class Deleting(ApiSuggestionExecutor):
        async def _done(self, job_id, status, error, fields=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(ApiTestSuggestion, job_id))
                await session.commit()
            return await super()._done(job_id, status, error, fields)

    deleting = Deleting(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([LLMError("x")]),
        sandbox=SuggestionSandbox(), images=Images(), max_tokens=10, max_endpoints=1,
    )  # fmt: skip
    assert await deleting.execute(job.id) is RunStatus.SUCCEEDED
    async with session_factory() as session:
        assert await session.get(ApiTestSuggestion, job.id) is None
