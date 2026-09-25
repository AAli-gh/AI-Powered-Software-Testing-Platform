from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from testmind.core.config import Settings


def create_engine(settings: Settings, *, pooled: bool = True) -> AsyncEngine:
    """Create the async engine.

    `pooled=False` is used by the Celery worker: each task runs its own event loop via
    `asyncio.run`, and asyncpg connections cannot be shared across event loops.
    """
    url = settings.database_url.get_secret_value()
    if not pooled:
        return create_async_engine(url, poolclass=NullPool)
    return create_async_engine(
        url,
        pool_size=settings.database_pool_size,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
