from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from testmind.db.models import AnalysisRun, TestGeneration


class GenerationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, generation: TestGeneration) -> TestGeneration:
        self._session.add(generation)
        await self._session.flush()
        return generation

    async def get(self, generation_id: uuid.UUID) -> TestGeneration | None:
        return await self._session.get(TestGeneration, generation_id)

    async def get_for_update(self, generation_id: uuid.UUID) -> TestGeneration | None:
        result = await self._session.execute(
            select(TestGeneration)
            .where(TestGeneration.id == generation_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def source_location(self, run_id: uuid.UUID) -> str | None:
        result = await self._session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .options(selectinload(AnalysisRun.project))
        )
        run = result.scalar_one_or_none()
        return run.project.source_location if run else None

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[TestGeneration], int]:
        rows = await self._session.execute(
            select(TestGeneration)
            .where(TestGeneration.run_id == run_id)
            .order_by(TestGeneration.created_at.desc(), TestGeneration.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(TestGeneration).where(TestGeneration.run_id == run_id)
        )
        return rows.scalars().all(), total.scalar_one()
