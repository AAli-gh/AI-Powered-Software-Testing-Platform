import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.db.models import AnalysisRun, Project
from testmind.domain.enums import RunStatus, SourceType
from testmind.services.run_executor import UNEXPECTED_ERROR_MESSAGE, RunExecutor


async def add_run(
    session_factory: async_sessionmaker[AsyncSession],
    status: RunStatus = RunStatus.QUEUED,
    location: str = "sample",
) -> uuid.UUID:
    async with session_factory() as session:
        project = Project(name="demo", source_type=SourceType.LOCAL, source_location=location)
        session.add(project)
        await session.flush()
        run = AnalysisRun(project_id=project.id, status=status)
        session.add(run)
        await session.commit()
        return run.id


async def load(session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID) -> AnalysisRun:
    async with session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        return run


async def test_queued_run_succeeds(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await add_run(session_factory)

    status = await RunExecutor(session_factory, repos_root).execute(run_id)

    run = await load(session_factory, run_id)
    assert status is RunStatus.SUCCEEDED
    assert run.status is RunStatus.SUCCEEDED
    assert run.started_at is not None
    assert run.finished_at is not None
    assert run.finished_at >= run.started_at
    assert run.error_message is None


async def test_missing_source_fails_the_run(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await add_run(session_factory)
    shutil.rmtree(repos_root / "sample")  # removed after the project was registered

    status = await RunExecutor(session_factory, repos_root).execute(run_id)

    run = await load(session_factory, run_id)
    assert status is RunStatus.FAILED
    assert run.error_message is not None
    assert "does not exist" in run.error_message


@pytest.mark.parametrize(
    "status", [RunStatus.CANCELLED, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.RUNNING]
)
async def test_non_queued_run_is_left_untouched(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path, status: RunStatus
) -> None:
    """Cancelled before pick-up, or re-delivered by the broker: never executed twice."""
    run_id = await add_run(session_factory, status=status)

    result = await RunExecutor(session_factory, repos_root).execute(run_id)

    run = await load(session_factory, run_id)
    assert result is status
    assert run.status is status
    assert run.started_at is None


async def test_executing_twice_is_idempotent(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await add_run(session_factory)
    executor = RunExecutor(session_factory, repos_root)
    await executor.execute(run_id)
    first = await load(session_factory, run_id)

    assert await executor.execute(run_id) is RunStatus.SUCCEEDED
    assert (await load(session_factory, run_id)).finished_at == first.finished_at


async def test_unknown_run_returns_none(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    assert await RunExecutor(session_factory, repos_root).execute(uuid.uuid4()) is None


class CrashingExecutor(RunExecutor):
    def _perform(self, source_location: str) -> None:
        raise RuntimeError("bug in analysis step")


async def test_unexpected_error_fails_run_and_propagates(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await add_run(session_factory)

    with pytest.raises(RuntimeError, match="bug in analysis step"):
        await CrashingExecutor(session_factory, repos_root).execute(run_id)

    run = await load(session_factory, run_id)
    assert run.status is RunStatus.FAILED
    # Internal details go to the logs, not to the user-visible error message.
    assert run.error_message == UNEXPECTED_ERROR_MESSAGE


async def test_run_deleted_while_running_is_tolerated(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await add_run(session_factory)
    deleted: list[bool] = []

    class DeletingExecutor(RunExecutor):
        async def _finish(
            self, run_id: uuid.UUID, status: RunStatus, error_message: str | None
        ) -> RunStatus:
            async with session_factory() as session:
                run = await session.get(AnalysisRun, run_id)
                await session.delete(run)
                await session.commit()
                deleted.append(True)
            return await super()._finish(run_id, status, error_message)

    assert (
        await DeletingExecutor(session_factory, repos_root).execute(run_id) is RunStatus.SUCCEEDED
    )
    assert deleted == [True]
