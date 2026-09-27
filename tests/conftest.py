"""Shared fixtures.

Integration and API tests run against *real* PostgreSQL and Redis (never mocks): locally via
`make test-services` (docker-compose.test.yml), in CI via GitHub Actions service containers.
Connection URLs can be overridden with TESTMIND_TEST_DATABASE_URL / TESTMIND_TEST_REDIS_URL.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from testmind.core.config import Environment, Settings
from testmind.db.session import create_engine, create_session_factory
from testmind.github.client import AppCredentials, GitHubClient
from tests.github_fake import APP_ID, FakeGitHub, private_key_pem
from tests.pull_fixture import BASE, HEAD

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TEST_DATABASE_URL = os.environ.get(
    "TESTMIND_TEST_DATABASE_URL",
    "postgresql+asyncpg://testmind:testmind@localhost:55432/testmind_test",
)
GITHUB_REPOSITORY = "acme/shop"
GITHUB_WEBHOOK_SECRET = "webhook-secret"
TEST_REDIS_URL = os.environ.get("TESTMIND_TEST_REDIS_URL", "redis://localhost:56379/15")

INTEGRATION_DIRS = ("integration", "api")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(f"tests/{name}/" in item.nodeid for name in INTEGRATION_DIRS):
            item.add_marker(pytest.mark.integration)
        if "tests/sandbox/" in item.nodeid:
            item.add_marker(pytest.mark.sandbox)


@pytest.fixture
def repos_root(tmp_path: Path) -> Path:
    """A repositories root containing one valid project directory: `sample`."""
    root = tmp_path / "repos"
    (root / "sample").mkdir(parents=True)
    (root / "sample" / "main.py").write_text("print('hello')\n")
    return root.resolve()


@pytest.fixture(scope="session")
def private_key() -> str:
    """A GitHub App private key, generated for the test session."""
    return private_key_pem()


@pytest.fixture
def github(private_key: str) -> FakeGitHub:
    """The fake GitHub API (tests/github_fake.py), knowing the App's public key."""
    return FakeGitHub(private_key)


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


@pytest.fixture
def github_settings(settings: Settings, private_key: str, tmp_path: Path) -> Settings:
    """Settings with the GitHub App configured, against the fake GitHub API."""
    return settings.model_copy(
        update={
            "github_app_id": APP_ID,
            "github_private_key": SecretStr(private_key),
            "github_webhook_secret": SecretStr(GITHUB_WEBHOOK_SECRET),
            "github_api_url": "http://github.test",
            "github_imports_root": tmp_path / "imports",
        }
    )


@pytest.fixture
def github_clients(github: FakeGitHub, private_key: str) -> Callable[[], GitHubClient]:
    """GitHub clients talking to the fake GitHub in process."""

    def create() -> GitHubClient:
        return GitHubClient(
            AppCredentials(APP_ID, private_key),
            api_url="http://github.test",
            transport=httpx.ASGITransport(app=github.app),
        )

    return create


@pytest.fixture
def shop(github: FakeGitHub) -> tuple[str, str]:
    """acme/shop with pull request 7 (tests/pull_fixture.py): base and head shas."""
    github.add_repository(GITHUB_REPOSITORY)
    base = github.add_commit(GITHUB_REPOSITORY, BASE, ref="main")
    head = github.add_commit(GITHUB_REPOSITORY, HEAD, ref="feature")
    github.add_pull(GITHUB_REPOSITORY, 7, base, head, title="Change pricing")
    return base, head


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
            text(
                "TRUNCATE github_imports, github_pull_analyses, api_test_suggestions, "
                "mutant_test_suggestions, mutation_runs, "
                "regressions, failure_analyses, test_executions, test_generations, "
                "code_analyses, repository_analyses, "
                "analysis_runs, projects CASCADE"
            )
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
