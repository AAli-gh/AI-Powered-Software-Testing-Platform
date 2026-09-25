from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import RepositoryAnalysis


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, analysis: RepositoryAnalysis) -> RepositoryAnalysis:
        self._session.add(analysis)
        await self._session.flush()
        return analysis

    async def get_for_run(self, run_id: uuid.UUID) -> RepositoryAnalysis | None:
        result = await self._session.execute(
            select(RepositoryAnalysis).where(RepositoryAnalysis.run_id == run_id)
        )
        return result.scalar_one_or_none()
