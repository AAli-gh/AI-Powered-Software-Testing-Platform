from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import CodeAnalysis, RepositoryAnalysis


class AnalysisRepository:
    """Per-run analysis documents (repository profile, code profile)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, analysis: RepositoryAnalysis | CodeAnalysis) -> None:
        self._session.add(analysis)
        await self._session.flush()

    async def get_for_run(self, run_id: uuid.UUID) -> RepositoryAnalysis | None:
        result = await self._session.execute(
            select(RepositoryAnalysis).where(RepositoryAnalysis.run_id == run_id)
        )
        return result.scalar_one_or_none()

    async def get_code_for_run(self, run_id: uuid.UUID) -> CodeAnalysis | None:
        result = await self._session.execute(
            select(CodeAnalysis).where(CodeAnalysis.run_id == run_id)
        )
        return result.scalar_one_or_none()
