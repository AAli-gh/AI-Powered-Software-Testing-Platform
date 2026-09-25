from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, project: Project) -> Project:
        self._session.add(project)
        await self._session.flush()
        return project

    async def get(self, project_id: uuid.UUID) -> Project | None:
        return await self._session.get(Project, project_id)

    async def get_by_name(self, name: str) -> Project | None:
        result = await self._session.execute(select(Project).where(Project.name == name))
        return result.scalar_one_or_none()

    async def list(self, *, offset: int, limit: int) -> Sequence[Project]:
        result = await self._session.execute(
            select(Project)
            .order_by(Project.created_at.desc(), Project.id)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()

    async def count(self) -> int:
        result = await self._session.execute(select(func.count()).select_from(Project))
        return result.scalar_one()

    async def delete(self, project: Project) -> None:
        await self._session.delete(project)
        await self._session.flush()
