from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from testmind.db.models import AnalysisRun
from testmind.domain.enums import ACTIVE_STATUSES


class RunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: AnalysisRun) -> AnalysisRun:
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> AnalysisRun | None:
        return await self._session.get(AnalysisRun, run_id)

    async def get_for_update(self, run_id: uuid.UUID) -> AnalysisRun | None:
        """Load a run with a row lock so status changes from the API and worker serialise."""
        result = await self._session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.id == run_id)
            .options(selectinload(AnalysisRun.project))
            .with_for_update(of=AnalysisRun)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_for_project(
        self, project_id: uuid.UUID, *, offset: int, limit: int
    ) -> Sequence[AnalysisRun]:
        result = await self._session.execute(
            select(AnalysisRun)
            .where(AnalysisRun.project_id == project_id)
            .order_by(AnalysisRun.created_at.desc(), AnalysisRun.id)
            .offset(offset)
            .limit(limit)
        )
        return result.scalars().all()

    async def count_for_project(self, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count())
            .select_from(AnalysisRun)
            .where(AnalysisRun.project_id == project_id)
        )
        return result.scalar_one()

    async def has_active_runs(self, project_id: uuid.UUID) -> bool:
        result = await self._session.execute(
            select(
                exists().where(
                    AnalysisRun.project_id == project_id,
                    AnalysisRun.status.in_(ACTIVE_STATUSES),
                )
            )
        )
        return bool(result.scalar_one())
