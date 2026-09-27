from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import ApiTestSuggestion


class ApiSuggestionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: ApiTestSuggestion) -> ApiTestSuggestion:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, suggestion_id: uuid.UUID) -> ApiTestSuggestion | None:
        return await self._session.get(ApiTestSuggestion, suggestion_id)

    async def lock(self, suggestion_id: uuid.UUID) -> ApiTestSuggestion | None:
        result = await self._session.execute(
            select(ApiTestSuggestion)
            .where(ApiTestSuggestion.id == suggestion_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def for_execution(self, execution_id: uuid.UUID) -> Sequence[ApiTestSuggestion]:
        rows = await self._session.execute(
            select(ApiTestSuggestion)
            .where(ApiTestSuggestion.execution_id == execution_id)
            .order_by(ApiTestSuggestion.created_at.desc(), ApiTestSuggestion.id)
        )
        return rows.scalars().all()
