from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.core.errors import ConflictError, NotFoundError
from testmind.db.models import Project
from testmind.repositories.project_repository import ProjectRepository
from testmind.repositories.run_repository import RunRepository
from testmind.schemas.project import ProjectCreate
from testmind.services.source_locator import resolve_local_source, to_stored_location


class ProjectService:
    def __init__(self, session: AsyncSession, repos_root: Path) -> None:
        self._session = session
        self._repos_root = repos_root
        self._projects = ProjectRepository(session)
        self._runs = RunRepository(session)

    async def create(self, data: ProjectCreate) -> Project:
        resolved = resolve_local_source(self._repos_root, data.source_location)

        if await self._projects.get_by_name(data.name) is not None:
            raise ConflictError(f"A project named '{data.name}' already exists")

        project = Project(
            name=data.name,
            source_type=data.source_type,
            source_location=to_stored_location(self._repos_root, resolved),
        )
        try:
            await self._projects.add(project)
            await self._session.commit()
        except IntegrityError:
            # Lost a race with a concurrent request creating the same name.
            await self._session.rollback()
            raise ConflictError(f"A project named '{data.name}' already exists") from None
        return project

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError(f"Project '{project_id}' not found")
        return project

    async def list(self, *, offset: int, limit: int) -> tuple[Sequence[Project], int]:
        return await self._projects.list(offset=offset, limit=limit), await self._projects.count()

    async def delete(self, project_id: uuid.UUID) -> None:
        project = await self.get(project_id)
        if await self._runs.has_active_runs(project_id):
            raise ConflictError("Project has queued or running analysis runs; cancel them first")
        await self._projects.delete(project)
        await self._session.commit()
