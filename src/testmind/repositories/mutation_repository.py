from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import MutantTestSuggestion, MutationRun

JobT = TypeVar("JobT", MutationRun, MutantTestSuggestion)


class MutationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: JobT) -> JobT:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, mutation_run_id: uuid.UUID) -> MutationRun | None:
        return await self._session.get(MutationRun, mutation_run_id)

    async def get_suggestion(self, suggestion_id: uuid.UUID) -> MutantTestSuggestion | None:
        return await self._session.get(MutantTestSuggestion, suggestion_id)

    async def lock(self, model: type[JobT], job_id: uuid.UUID) -> JobT | None:
        result = await self._session.execute(
            select(model)
            .where(model.id == job_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[MutationRun], int]:
        condition = MutationRun.run_id == run_id
        rows = await self._session.execute(
            select(MutationRun)
            .where(condition)
            .order_by(MutationRun.created_at.desc(), MutationRun.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(MutationRun).where(condition)
        )
        return rows.scalars().all(), total.scalar_one()

    async def suggestions_for(self, mutation_run_id: uuid.UUID) -> Sequence[MutantTestSuggestion]:
        rows = await self._session.execute(
            select(MutantTestSuggestion)
            .where(MutantTestSuggestion.mutation_run_id == mutation_run_id)
            .order_by(MutantTestSuggestion.created_at.desc(), MutantTestSuggestion.id)
        )
        return rows.scalars().all()
