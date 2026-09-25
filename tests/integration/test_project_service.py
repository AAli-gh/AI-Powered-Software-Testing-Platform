import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.errors import ConflictError, InvalidSourceError, NotFoundError
from testmind.db.models import AnalysisRun
from testmind.domain.enums import RunStatus, SourceType
from testmind.schemas.project import ProjectCreate
from testmind.services.project_service import ProjectService


def service(session: AsyncSession, repos_root: Path) -> ProjectService:
    return ProjectService(session, repos_root)


async def test_create_persists_relative_location(session: AsyncSession, repos_root: Path) -> None:
    project = await service(session, repos_root).create(
        ProjectCreate(name="demo", source_location=str(repos_root / "sample"))
    )
    assert project.source_location == "sample"
    assert project.source_type is SourceType.LOCAL
    assert project.created_at is not None

    fetched = await service(session, repos_root).get(project.id)
    assert fetched.name == "demo"


async def test_create_rejects_invalid_source(session: AsyncSession, repos_root: Path) -> None:
    with pytest.raises(InvalidSourceError):
        await service(session, repos_root).create(
            ProjectCreate(name="demo", source_location="../../etc")
        )
    assert (await service(session, repos_root).list(offset=0, limit=10))[1] == 0


async def test_duplicate_name_conflicts(session: AsyncSession, repos_root: Path) -> None:
    svc = service(session, repos_root)
    await svc.create(ProjectCreate(name="demo", source_location="sample"))
    with pytest.raises(ConflictError, match="already exists"):
        await svc.create(ProjectCreate(name="demo", source_location="sample"))


async def test_unique_constraint_race_maps_to_conflict(
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two sessions pass the name pre-check; the DB unique constraint must still win."""
    async with session_factory() as first, session_factory() as second:
        svc_first, svc_second = service(first, repos_root), service(second, repos_root)
        await svc_first.create(ProjectCreate(name="race", source_location="sample"))
        # Bypass the pre-check to simulate the second request having read before the insert.
        monkeypatch.setattr(svc_second._projects, "get_by_name", _none)
        with pytest.raises(ConflictError):
            await svc_second.create(ProjectCreate(name="race", source_location="sample"))


async def _none(_: str) -> None:
    return None


async def test_list_is_paginated_newest_first(session: AsyncSession, repos_root: Path) -> None:
    svc = service(session, repos_root)
    for index in range(3):
        await svc.create(ProjectCreate(name=f"p{index}", source_location="sample"))

    items, total = await svc.list(offset=0, limit=2)
    assert total == 3
    assert [p.name for p in items] == ["p2", "p1"]

    items, _ = await svc.list(offset=2, limit=2)
    assert [p.name for p in items] == ["p0"]


async def test_get_missing_raises(session: AsyncSession, repos_root: Path) -> None:
    with pytest.raises(NotFoundError):
        await service(session, repos_root).get(uuid.uuid4())


async def test_delete_cascades_finished_runs(session: AsyncSession, repos_root: Path) -> None:
    svc = service(session, repos_root)
    project = await svc.create(ProjectCreate(name="demo", source_location="sample"))
    session.add(AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED))
    await session.commit()

    await svc.delete(project.id)

    with pytest.raises(NotFoundError):
        await svc.get(project.id)
    assert await session.get(AnalysisRun, project.id) is None


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
async def test_delete_with_active_run_conflicts(
    session: AsyncSession, repos_root: Path, status: RunStatus
) -> None:
    svc = service(session, repos_root)
    project = await svc.create(ProjectCreate(name="demo", source_location="sample"))
    session.add(AnalysisRun(project_id=project.id, status=status))
    await session.commit()

    with pytest.raises(ConflictError, match="queued or running"):
        await svc.delete(project.id)
