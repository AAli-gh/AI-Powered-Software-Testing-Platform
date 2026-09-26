from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from testmind.db.models import Regression


class RegressionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, regression: Regression) -> Regression:
        self._session.add(regression)
        await self._session.flush()
        return regression

    async def get(self, regression_id: uuid.UUID) -> Regression | None:
        return await self._session.get(Regression, regression_id)

    async def get_for_update(self, regression_id: uuid.UUID) -> Regression | None:
        result = await self._session.execute(
            select(Regression)
            .where(Regression.id == regression_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def source_location(self, regression_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(Regression)
            .where(Regression.id == regression_id)
            .options(selectinload(Regression.project))
        )
        regression = result.scalar_one_or_none()
        return regression.project.source_location if regression else None

    async def list_for_project(
        self, project_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[Regression], int]:
        condition = Regression.project_id == project_id
        rows = await self._session.execute(
            select(Regression)
            .where(condition)
            .order_by(Regression.created_at.desc(), Regression.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(Regression).where(condition)
        )
        return rows.scalars().all(), total.scalar_one()
