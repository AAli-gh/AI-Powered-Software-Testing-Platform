from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import TestExecution


class ExecutionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, execution: TestExecution) -> TestExecution:
        self._session.add(execution)
        await self._session.flush()
        return execution

    async def get(self, execution_id: uuid.UUID) -> TestExecution | None:
        return await self._session.get(TestExecution, execution_id)

    async def get_for_update(self, execution_id: uuid.UUID) -> TestExecution | None:
        result = await self._session.execute(
            select(TestExecution)
            .where(TestExecution.id == execution_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def list_for_run(
        self, run_id: uuid.UUID, *, offset: int, limit: int
    ) -> tuple[Sequence[TestExecution], int]:
        rows = await self._session.execute(
            select(TestExecution)
            .where(TestExecution.run_id == run_id)
            .order_by(TestExecution.created_at.desc(), TestExecution.id)
            .offset(offset)
            .limit(limit)
        )
        total = await self._session.execute(
            select(func.count()).select_from(TestExecution).where(TestExecution.run_id == run_id)
        )
        return rows.scalars().all(), total.scalar_one()
