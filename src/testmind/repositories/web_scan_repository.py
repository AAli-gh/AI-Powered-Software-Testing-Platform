from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import WebScan


class WebScanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, scan: WebScan) -> WebScan:
        self._session.add(scan)
        await self._session.flush()
        return scan

    async def get(self, scan_id: uuid.UUID) -> WebScan | None:
        return await self._session.get(WebScan, scan_id)

    async def lock(self, scan_id: uuid.UUID) -> WebScan | None:
        result = await self._session.execute(
            select(WebScan)
            .where(WebScan.id == scan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def page(self, *, limit: int, offset: int) -> tuple[Sequence[WebScan], int]:
        total = await self._session.scalar(select(func.count()).select_from(WebScan)) or 0
        rows = await self._session.execute(
            select(WebScan)
            .order_by(WebScan.created_at.desc(), WebScan.id)
            .limit(limit)
            .offset(offset)
        )
        return rows.scalars().all(), total
