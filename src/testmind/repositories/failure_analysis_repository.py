from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import FailureAnalysis


class FailureAnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, analysis: FailureAnalysis) -> FailureAnalysis:
        self._session.add(analysis)
        await self._session.flush()
        return analysis

    async def get(self, analysis_id: uuid.UUID) -> FailureAnalysis | None:
        return await self._session.get(FailureAnalysis, analysis_id)

    async def get_for_update(self, analysis_id: uuid.UUID) -> FailureAnalysis | None:
        result = await self._session.execute(
            select(FailureAnalysis)
            .where(FailureAnalysis.id == analysis_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_for_execution(
        self, execution_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[FailureAnalysis], int]:
        condition = FailureAnalysis.execution_id == execution_id
        rows = await self._session.execute(
            select(FailureAnalysis)
            .where(condition)
            .order_by(FailureAnalysis.created_at.desc(), FailureAnalysis.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(FailureAnalysis).where(condition)
        )
        return rows.scalars().all(), total.scalar_one()
