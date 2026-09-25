"""Shared fixtures.

Integration and API tests run against *real* PostgreSQL and Redis (never mocks): locally via
`make test-services` (docker-compose.test.yml), in CI via GitHub Actions service containers.
Connection URLs can be overridden with TESTMIND_TEST_DATABASE_URL / TESTMIND_TEST_REDIS_URL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from testmind.core.config import Environment, Settings
from testmind.db.session import create_engine, create_session_factory

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEST_DATABASE_URL = os.environ.get(
    "TESTMIND_TEST_DATABASE_URL",
    "postgresql+asyncpg://testmind:testmind@localhost:55432/testmind_test",
)
TEST_REDIS_URL = os.environ.get("TESTMIND_TEST_REDIS_URL", "redis://localhost:56379/15")

INTEGRATION_DIRS = ("integration", "api")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(f"tests/{name}/" in item.nodeid for name in INTEGRATION_DIRS):
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def repos_root(tmp_path: Path) -> Path:
    """A repositories root containing one valid project directory: `sample`."""
    root = tmp_path / "repos"
    (root / "sample").mkdir(parents=True)
    (root / "sample" / "main.py").write_text("print('hello')\n")
    return root.resolve()


@pytest.fixture
def settings(repos_root: Path) -> Settings:
    return Settings(
        _env_file=None,
        env=Environment.TEST,
        database_url=TEST_DATABASE_URL,  # type: ignore[arg-type]
        redis_url=TEST_REDIS_URL,  # type: ignore[arg-type]
        repos_root=repos_root,
        log_json=False,
    )


def alembic_config(database_url: str = TEST_DATABASE_URL) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session")
def migrated_database() -> None:
    """Build the schema once per test session through the real migrations."""
    config = alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, "head")


@pytest.fixture
async def engine(migrated_database: None, settings: Settings) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings, pooled=False)
    async with engine.begin() as connection:
        await connection.execute(
            text("TRUNCATE repository_analyses, analysis_runs, projects CASCADE")
        )
    yield engine
    await engine.dispose()


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
