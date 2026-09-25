import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import DispatchError, InvalidStateTransitionError, NotFoundError
from testmind.db.models import AnalysisRun, Project
from testmind.domain.enums import RunStatus
from testmind.schemas.project import ProjectCreate
from testmind.services.project_service import ProjectService
from testmind.services.run_service import DISPATCH_FAILED_MESSAGE, RunService
from tests.doubles import FailingDispatcher, RecordingDispatcher


async def make_project(session: AsyncSession, repos_root: Path, name: str = "demo") -> Project:
    return await ProjectService(session, repos_root).create(
        ProjectCreate(name=name, source_location="sample")
    )


async def test_create_commits_then_dispatches(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    project = await make_project(session, repos_root)
    seen_by_other_session: list[RunStatus | None] = []

    class VisibilityCheckingDispatcher(RecordingDispatcher):
        async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
            # The worker uses a different connection: the row must already be committed.
            async with session_factory() as other:
                row = await other.get(AnalysisRun, run_id)
                seen_by_other_session.append(row.status if row else None)
            await super().dispatch_analysis_run(run_id)

    dispatcher = VisibilityCheckingDispatcher()
    run = await RunService(session, dispatcher).create(project.id)

    assert run.status is RunStatus.QUEUED
    assert dispatcher.dispatched == [run.id]
    assert seen_by_other_session == [RunStatus.QUEUED]


async def test_create_for_missing_project(session: AsyncSession) -> None:
    dispatcher = RecordingDispatcher()
    with pytest.raises(NotFoundError):
        await RunService(session, dispatcher).create(uuid.uuid4())
    assert dispatcher.dispatched == []


async def test_dispatch_failure_marks_run_failed(session: AsyncSession, repos_root: Path) -> None:
    project = await make_project(session, repos_root)
    service = RunService(session, FailingDispatcher())

    with pytest.raises(DispatchError):
        await service.create(project.id)

    runs, total = await service.list_for_project(project.id, offset=0, limit=10)
    assert total == 1
    assert runs[0].status is RunStatus.FAILED
    assert runs[0].error_message == DISPATCH_FAILED_MESSAGE
    assert runs[0].finished_at is not None


async def test_dispatch_failure_does_not_override_a_concurrent_cancel(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    project = await make_project(session, repos_root)

    class CancelThenFailDispatcher(FailingDispatcher):
        async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
            async with session_factory() as other:
                await RunService(other, RecordingDispatcher()).cancel(run_id)
            await super().dispatch_analysis_run(run_id)

    service = RunService(session, CancelThenFailDispatcher())
    with pytest.raises(DispatchError):
        await service.create(project.id)

    runs, _ = await service.list_for_project(project.id, offset=0, limit=10)
    assert runs[0].status is RunStatus.CANCELLED
    assert runs[0].error_message is None


async def test_cancel_queued_run(session: AsyncSession, repos_root: Path) -> None:
    project = await make_project(session, repos_root)
    service = RunService(session, RecordingDispatcher())
    run = await service.create(project.id)

    cancelled = await service.cancel(run.id)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.finished_at is not None
    # Server-generated timestamps are readable right after the UPDATE (no implicit async I/O).
    assert cancelled.updated_at >= cancelled.created_at


@pytest.mark.parametrize("status", [RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.CANCELLED])
async def test_cancel_rejected_outside_queued(
    session: AsyncSession, repos_root: Path, status: RunStatus
) -> None:
    project = await make_project(session, repos_root)
    run = AnalysisRun(project_id=project.id, status=status)
    session.add(run)
    await session.commit()
    run_id = run.id  # rollback() expires loaded objects; keep the plain value

    service = RunService(session, RecordingDispatcher())
    with pytest.raises(InvalidStateTransitionError):
        await service.cancel(run_id)
    assert (await service.get(run_id)).status is status


async def test_cancel_missing_run(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await RunService(session, RecordingDispatcher()).cancel(uuid.uuid4())


async def test_get_missing_run(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await RunService(session, RecordingDispatcher()).get(uuid.uuid4())


async def test_list_for_missing_project(session: AsyncSession) -> None:
    with pytest.raises(NotFoundError):
        await RunService(session, RecordingDispatcher()).list_for_project(
            uuid.uuid4(), offset=0, limit=10
        )


async def test_list_for_project_only_returns_its_runs(
    session: AsyncSession, repos_root: Path
) -> None:
    first = await make_project(session, repos_root, "first")
    second = await make_project(session, repos_root, "second")
    service = RunService(session, RecordingDispatcher())
    mine = [await service.create(first.id) for _ in range(3)]
    await service.create(second.id)

    runs, total = await service.list_for_project(first.id, offset=0, limit=2)

    assert total == 3
    assert len(runs) == 2
    assert {run.id for run in runs} <= {run.id for run in mine}


async def test_row_lock_serialises_concurrent_cancels(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    """Exactly one of two concurrent cancels wins; the other sees the terminal state."""
    async with session_factory() as session:
        project = await make_project(session, repos_root)
        run = await RunService(session, RecordingDispatcher()).create(project.id)

    async def cancel() -> RunStatus | str:
        async with session_factory() as session:
            try:
                return (await RunService(session, RecordingDispatcher()).cancel(run.id)).status
            except InvalidStateTransitionError:
                return "rejected"

    results = await asyncio.gather(cancel(), cancel())
    assert sorted(map(str, results)) == ["cancelled", "rejected"]
