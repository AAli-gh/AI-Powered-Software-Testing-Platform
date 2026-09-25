import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncEngine

import testmind.db.models  # noqa: F401
from testmind.db.base import Base
from tests.conftest import alembic_config


def _diff(connection: Connection) -> list[object]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


async def test_migrations_match_the_orm_models(engine: AsyncEngine) -> None:
    """Fails when a model changes without a matching migration."""
    async with engine.connect() as connection:
        assert await connection.run_sync(_diff) == []


async def test_migrations_are_reversible(engine: AsyncEngine) -> None:
    # Alembic's env.py runs its own event loop, so drive it from a worker thread.
    config = alembic_config()
    await asyncio.to_thread(command.downgrade, config, "base")
    async with engine.connect() as connection:
        tables = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        assert set(tables.scalars()) <= {"alembic_version"}
    await asyncio.to_thread(command.upgrade, config, "head")
    async with engine.connect() as connection:
        assert await connection.run_sync(_diff) == []
