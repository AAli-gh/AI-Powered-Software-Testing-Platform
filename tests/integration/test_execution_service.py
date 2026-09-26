import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.ai_engine.test_generator.models import GenerationOptions
from testmind.core.errors import ConflictError, DispatchError, NotFoundError
from testmind.db.models import AnalysisRun, TestExecution, TestGeneration
from testmind.domain.enums import RunStatus
from testmind.sandbox.models import Outcome
from testmind.schemas.project import ProjectCreate
from testmind.services.execution_executor import NO_REPOSITORY_ANALYSIS, ExecutionExecutor
from testmind.services.execution_service import ExecutionService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, RunService
from testmind.test_engine.models import ExecutionOutcome, TestRunReport
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import FailingDispatcher, RecordingDispatcher
from tests.integration.test_generation_service import analysed_run
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

GENERATED_CODE = "def test_generated():\n    assert True\n"


def runner(outcome: Outcome = Outcome.PASSED) -> tuple[TestSuiteRunner, Sandbox]:
    sandbox = Sandbox(
        result(case("test_x", outcome), exit_code=0 if outcome is Outcome.PASSED else 1)
    )
    return TestSuiteRunner(sandbox, Images()), sandbox


async def add_generation(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, status: RunStatus
) -> uuid.UUID:
    async with session_factory() as session:
        generation = TestGeneration(
            run_id=run_id,
            status=status,
            options=GenerationOptions().model_dump(mode="json"),
            provider="anthropic",
            result={
                "files": [
                    _file("tests/testmind/test_main.py", "main", "passed", GENERATED_CODE),
                    _file("tests/testmind/test_other.py", "other", "rejected", None),
                ],
                "model": "m", "prompt_version": "v1", "input_tokens": 1, "output_tokens": 1,
                "targets_selected": 1, "targets_available": 1,
            },
        )  # fmt: skip
        session.add(generation)
        await session.commit()
        return generation.id


def _file(path: str, module: str, status: str, code: str | None) -> dict[str, object]:
    return {
        "path": path,
        "module": module,
        "targets": [],
        "status": status,
        "code": code,
        "test_count": 1 if code else 0,
        "iterations": 1,
        "run": None,
        "suspected_bugs": [],
        "attempts": [],
    }


async def stored(
    session_factory: async_sessionmaker[AsyncSession], execution_id: uuid.UUID
) -> TestExecution:
    async with session_factory() as session:
        execution = await session.get(TestExecution, execution_id)
        assert execution is not None
        return execution


async def test_create_and_execute_with_generated_tests(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    (repos_root / "sample" / "tests").mkdir()
    (repos_root / "sample" / "tests" / "test_main.py").write_text("def test_a(): pass\n")
    generation_id = await add_generation(session_factory, run_id, RunStatus.SUCCEEDED)
    dispatcher = RecordingDispatcher()
    async with session_factory() as session:
        execution = await ExecutionService(session, dispatcher).create(run_id, generation_id)
    assert dispatcher.executions == [execution.id]

    suite_runner, sandbox = runner()
    status = await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(
        execution.id
    )

    assert status is RunStatus.SUCCEEDED
    row = await stored(session_factory, execution.id)
    assert row.outcome is ExecutionOutcome.PASSED
    assert row.generation_id == generation_id
    report = TestRunReport.model_validate(row.report)
    assert report.totals.passed == 1
    # the run's own analysis was produced before the tests directory existed
    assert report.project_test_files == []
    assert sandbox.jobs[0].test_files == {"testmind/test_main.py": GENERATED_CODE}

    # re-delivered message: no second execution
    assert (
        await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(execution.id)
        is RunStatus.SUCCEEDED
    )
    assert len(sandbox.jobs) == 1


async def test_failing_suite_is_a_succeeded_job_with_failed_outcome(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    (repos_root / "sample" / "test_x.py").write_text("def test_x(): pass\n")
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        execution = await ExecutionService(session, RecordingDispatcher()).create(run_id, None)
    suite_runner, _ = runner(Outcome.FAILED)
    await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(execution.id)
    row = await stored(session_factory, execution.id)
    assert row.status is RunStatus.SUCCEEDED
    assert row.outcome is ExecutionOutcome.FAILED


async def test_validation(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    other_run = await analysed_run_named(session_factory, repos_root, "other")
    pending = await add_generation(session_factory, run_id, RunStatus.RUNNING)
    foreign = await add_generation(session_factory, other_run, RunStatus.SUCCEEDED)
    async with session_factory() as session:
        service = ExecutionService(session, RecordingDispatcher())
        with pytest.raises(NotFoundError):
            await service.create(uuid.uuid4(), None)
        with pytest.raises(NotFoundError, match="not found for this run"):
            await service.create(run_id, foreign)
        with pytest.raises(NotFoundError):
            await service.create(run_id, uuid.uuid4())
        with pytest.raises(ConflictError, match="succeeded test generation"):
            await service.create(run_id, pending)
        with pytest.raises(NotFoundError):
            await service.get(uuid.uuid4())
        with pytest.raises(NotFoundError):
            await service.list_for_run(uuid.uuid4(), offset=0, limit=5)


async def analysed_run_named(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, name: str
) -> uuid.UUID:

    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name=name, source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    return run.id


async def test_requires_succeeded_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:

    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="q", source_location="sample")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
        with pytest.raises(ConflictError, match="status: queued"):
            await ExecutionService(session, RecordingDispatcher()).create(run.id, None)


async def test_dispatch_failure(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await ExecutionService(session, FailingDispatcher()).create(run_id, None)
        [execution], total = await ExecutionService(session, RecordingDispatcher()).list_for_run(
            run_id, offset=0, limit=5
        )
    assert total == 1
    assert execution.status is RunStatus.FAILED
    assert execution.error_message == DISPATCH_FAILED_MESSAGE


async def test_dispatch_failure_after_concurrent_delete(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)

    class DeleteThenFail(FailingDispatcher):
        async def dispatch_test_execution(self, execution_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await other.delete(await other.get(TestExecution, execution_id))
                await other.commit()
            await super().dispatch_test_execution(execution_id)

    async with session_factory() as session:
        with pytest.raises(DispatchError):
            await ExecutionService(session, DeleteThenFail()).create(run_id, None)


async def queued_execution(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID,
    generation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with session_factory() as session:
        execution = TestExecution(
            run_id=run_id, generation_id=generation_id, status=RunStatus.QUEUED
        )
        session.add(execution)
        await session.commit()
        return execution.id


async def test_run_without_analysis(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:

    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="n", source_location="sample")
        )
        run = AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED)
        session.add(run)
        await session.commit()
        run_id = run.id
    execution_id = await queued_execution(session_factory, run_id)
    suite_runner, _ = runner()
    assert (
        await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(execution_id)
        is RunStatus.FAILED
    )
    assert (await stored(session_factory, execution_id)).error_message == NO_REPOSITORY_ANALYSIS


async def test_missing_source_and_crash(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:

    run_id = await analysed_run(session_factory, repos_root)
    first = await queued_execution(session_factory, run_id)
    second = await queued_execution(session_factory, run_id)

    class Crashing(TestSuiteRunner):
        def run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError):
        await ExecutionExecutor(session_factory, repos_root, Crashing(Sandbox(), Images())).execute(
            first
        )
    assert (await stored(session_factory, first)).error_message == UNEXPECTED_ERROR_MESSAGE

    shutil.rmtree(repos_root / "sample")
    suite_runner, _ = runner()
    assert (
        await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(second)
        is RunStatus.FAILED
    )
    assert "does not exist" in ((await stored(session_factory, second)).error_message or "")


async def test_unknown_and_vanishing(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    suite_runner, _ = runner()
    assert (
        await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(uuid.uuid4())
        is None
    )

    run_id = await analysed_run(session_factory, repos_root)
    execution_id = await queued_execution(session_factory, run_id, generation_id=None)

    class Deleting(ExecutionExecutor):
        async def _finish(self, execution_id, status, error_message, report=None):  # type: ignore[no-untyped-def]
            async with session_factory() as session:
                await session.delete(await session.get(TestExecution, execution_id))
                await session.commit()
            return await super()._finish(execution_id, status, error_message, report)

    assert (
        await Deleting(session_factory, repos_root, suite_runner).execute(execution_id)
        is RunStatus.SUCCEEDED
    )


async def test_generation_without_result_contributes_nothing(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed_run(session_factory, repos_root)
    async with session_factory() as session:
        generation = TestGeneration(
            run_id=run_id, status=RunStatus.SUCCEEDED, options={}, provider="anthropic"
        )
        session.add(generation)
        await session.commit()
        generation_id = generation.id
    execution_id = await queued_execution(session_factory, run_id, generation_id)
    suite_runner, sandbox = runner()
    await ExecutionExecutor(session_factory, repos_root, suite_runner).execute(execution_id)
    assert sandbox.jobs == []  # no project tests and no generated files -> no_tests
    assert (await stored(session_factory, execution_id)).outcome is ExecutionOutcome.NO_TESTS
