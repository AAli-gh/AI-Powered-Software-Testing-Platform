from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from testmind.db.models import AuthorizedTarget


class AuthorizedTargetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, target: AuthorizedTarget) -> AuthorizedTarget:
        self._session.add(target)
        await self._session.flush()
        return target

    async def get(self, target_id: uuid.UUID) -> AuthorizedTarget | None:
        return await self._session.get(AuthorizedTarget, target_id)

    async def by_host(self, host: str) -> AuthorizedTarget | None:
        result = await self._session.execute(
            select(AuthorizedTarget).where(AuthorizedTarget.host == host)
        )
        return result.scalar_one_or_none()

    async def all(self) -> Sequence[AuthorizedTarget]:
        rows = await self._session.execute(
            select(AuthorizedTarget).order_by(
                AuthorizedTarget.created_at.desc(), AuthorizedTarget.id
            )
        )
        return rows.scalars().all()
