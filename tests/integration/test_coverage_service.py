"""Coverage persistence and coverage-guided generation against the real database.

The sandbox is a canned double here (it returns a coverage.py JSON report); the real sandbox
path is covered by tests/sandbox/test_coverage.py and test_coverage_guided_generation.py.
"""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions, GenerationResult
from testmind.core.config import Settings
from testmind.core.errors import ConflictError, NotFoundError
from testmind.db.models import AnalysisRun, TestExecution, TestGeneration
from testmind.domain.enums import RunStatus
from testmind.sandbox.models import Outcome
from testmind.schemas.test_execution import TestExecutionRead
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.generation_executor import NO_COVERAGE
from testmind.services.generation_service import GenerationService
from testmind.services.run_executor import RunExecutor
from testmind.services.run_service import RunService
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import RecordingDispatcher, ScriptedLLM
from tests.integration.test_generation_service import (
    analysed_run,
    executor,
    llm_configured,  # noqa: F401  (fixture)
)
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

# main.py of the `analysed_run` project: `greet` (lines 1-2) never ran.
COVERAGE_JSON = json.dumps(
    {
        "meta": {"version": "7.16.1", "branch_coverage": True},
        "files": {"main.py": {"executed_lines": [1], "missing_lines": [2]}},
    }
)
GENERATED = (
    "```python\nfrom main import greet\n\ndef test_greet():\n    assert greet() == 'hi'\n```"
)


async def execute(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: uuid.UUID,
    coverage_json: str | None = COVERAGE_JSON,
) -> uuid.UUID:
    async with session_factory() as session:
        execution = await ExecutionService(session, RecordingDispatcher()).create(run_id, None)
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), coverage_json=coverage_json))
    await ExecutionExecutor(
        session_factory, repos_root, TestSuiteRunner(sandbox, Images())
    ).execute(execution.id)
    return execution.id


async def tested_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    """An analysed run of `sample` (main.py + a test module)."""
    (repos_root / "sample" / "test_main.py").write_text("def test_a(): pass\n")
    return await analysed_run(session_factory, repos_root)


async def another_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, run_id: uuid.UUID
) -> uuid.UUID:
    async with session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        other = await RunService(session, RecordingDispatcher()).create(run.project_id)
    await RunExecutor(session_factory, repos_root).execute(other.id)
    return other.id


async def test_coverage_is_stored_with_the_execution(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await tested_run(session_factory, repos_root)
    execution_id = await execute(session_factory, repos_root, run_id)

    async with session_factory() as session:
        service = ExecutionService(session, RecordingDispatcher())
        coverage = await service.coverage(execution_id)
        row = await service.get(execution_id)
        read = TestExecutionRead.from_row(row)

    assert [f.path for f in coverage.files] == ["main.py"]
    [function] = coverage.functions
    assert (function.qualname, function.percent) == ("greet", 0.0)
    assert [g.qualname for g in coverage.gaps] == ["greet"]
    assert row.coverage_percent == coverage.totals.percent == 50.0
    assert read.summary is not None
    assert (read.summary.coverage_percent, read.summary.coverage_error) == (50.0, None)


async def test_missing_coverage_is_explained(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await tested_run(session_factory, repos_root)
    without = await execute(session_factory, repos_root, run_id, coverage_json=None)
    async with session_factory() as session:
        service = ExecutionService(session, RecordingDispatcher())
        queued = await service.create(run_id, None)
        with pytest.raises(NotFoundError, match=r"coverage\.py wrote no report"):
            await service.coverage(without)
        with pytest.raises(NotFoundError, match=r"has not finished \(status: queued\)"):
            await service.coverage(queued.id)
        read = TestExecutionRead.from_row(await service.get(without))
    assert read.summary is not None
    assert read.summary.coverage_percent is None


async def test_coverage_guided_generation_is_validated(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await tested_run(session_factory, repos_root)
    other_run = await another_run(session_factory, repos_root, run_id)
    await execute(session_factory, repos_root, run_id)
    unmeasured = await execute(session_factory, repos_root, run_id, coverage_json=None)
    foreign = await execute(session_factory, repos_root, other_run)

    async with session_factory() as session:
        service = GenerationService(session, RecordingDispatcher(), settings)
        for missing in (uuid.uuid4(), foreign):
            with pytest.raises(NotFoundError, match="not found for this run"):
                await service.create(run_id, GenerationOptions(coverage_execution_id=missing))
        with pytest.raises(ConflictError, match="needs a test execution with a coverage report"):
            await service.create(run_id, GenerationOptions(coverage_execution_id=unmeasured))


@pytest.mark.usefixtures("llm_configured")
async def test_coverage_guided_generation(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await tested_run(session_factory, repos_root)
    execution_id = await execute(session_factory, repos_root, run_id)
    async with session_factory() as session:
        generation = await GenerationService(session, RecordingDispatcher(), settings).create(
            run_id, GenerationOptions(coverage_execution_id=execution_id)
        )

    llm = ScriptedLLM([GENERATED])
    status = await executor(session_factory, repos_root, llm).execute(generation.id)

    assert status is RunStatus.SUCCEEDED
    async with session_factory() as session:
        row = await session.get(TestGeneration, generation.id)
    assert row is not None
    assert row.prompt_version == "v1+coverage.v1"
    stored = GenerationResult.model_validate(row.result)
    assert [f.targets for f in stored.files] == [["greet"]]
    prompt = llm.requests[0].prompt
    assert "Coverage of these functions by the project's existing test suite" in prompt
    assert "- greet: 0.0% covered; lines never executed: 2" in prompt


@pytest.mark.usefixtures("llm_configured")
async def test_coverage_source_deleted_before_the_worker_ran(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, settings: Settings
) -> None:
    run_id = await tested_run(session_factory, repos_root)
    execution_id = await execute(session_factory, repos_root, run_id)
    async with session_factory() as session:
        generation = await GenerationService(session, RecordingDispatcher(), settings).create(
            run_id, GenerationOptions(coverage_execution_id=execution_id)
        )
        await session.delete(await session.get(TestExecution, execution_id))
        await session.commit()

    llm = ScriptedLLM([])
    status = await executor(session_factory, repos_root, llm).execute(generation.id)

    assert status is RunStatus.FAILED
    async with session_factory() as session:
        row = await session.get(TestGeneration, generation.id)
    assert row is not None
    assert row.error_message == NO_COVERAGE
    assert llm.requests == []
