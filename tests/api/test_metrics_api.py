"""/metrics over HTTP: the platform's state read from the real PostgreSQL and Redis, and the
HTTP metrics recorded by the middleware."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from testmind import __version__
from testmind.api.app import create_app
from testmind.api.routes.metrics import QUEUE_KEYS
from testmind.core.config import Settings
from testmind.db.models import AnalysisRun, Project, TestExecution
from testmind.domain.enums import RunStatus, SourceType
from tests.api.test_security_api import TOKEN, client_for
from tests.conftest import TEST_REDIS_URL

Samples = dict[tuple[str, tuple[tuple[str, str], ...]], float]


def samples(text: str) -> Samples:
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
    }


def value(parsed: Samples, name: str, **labels: str) -> float:
    return parsed[(name, tuple(sorted(labels.items())))]


@pytest.fixture
async def queue() -> AsyncIterator[Redis]:
    redis = Redis.from_url(TEST_REDIS_URL)
    await redis.delete(*QUEUE_KEYS)
    yield redis
    await redis.delete(*QUEUE_KEYS)
    await redis.aclose()


async def test_metrics_report_jobs_ages_and_queue(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    queue: Redis,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        project = Project(name="shop", source_type=SourceType.LOCAL, source_location="sample")
        session.add(project)
        await session.flush()
        running = AnalysisRun(
            project_id=project.id, status=RunStatus.RUNNING, started_at=now - timedelta(minutes=5)
        )
        done = AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED)
        session.add_all([running, done])
        await session.flush()
        waiting = TestExecution(
            run_id=done.id, status=RunStatus.QUEUED, created_at=now - timedelta(minutes=10)
        )
        session.add(waiting)
        await session.commit()
    # redis-py types rpush for both its clients; this one is asynchronous.
    await cast("Awaitable[int]", queue.rpush(QUEUE_KEYS[0], "a", "b"))
    await cast("Awaitable[int]", queue.rpush(QUEUE_KEYS[2], "c"))  # sent with a priority

    answer = await client.get("/metrics")

    assert answer.status_code == 200
    assert answer.headers["content-type"] == CONTENT_TYPE_LATEST
    parsed = samples(answer.text)
    assert value(parsed, "testmind_jobs", kind="analysis_runs", status="running") == 1
    assert value(parsed, "testmind_jobs", kind="analysis_runs", status="succeeded") == 1
    assert value(parsed, "testmind_jobs", kind="test_executions", status="queued") == 1
    assert value(parsed, "testmind_jobs", kind="github_imports", status="failed") == 0
    assert len([key for key in parsed if key[0] == "testmind_jobs"]) == 10 * len(RunStatus)
    queued_age = value(parsed, "testmind_oldest_job_age_seconds", status="queued")
    running_age = value(parsed, "testmind_oldest_job_age_seconds", status="running")
    assert 600 <= queued_age < 700
    assert 300 <= running_age < 400
    assert value(parsed, "testmind_queue_messages") == 3
    assert value(parsed, "testmind_dependency_up", dependency="database") == 1
    assert value(parsed, "testmind_dependency_up", dependency="redis") == 1
    assert value(parsed, "testmind_build_info", version=__version__) == 1


async def test_an_empty_platform_has_zero_ages(client: httpx.AsyncClient, queue: Redis) -> None:
    parsed = samples((await client.get("/metrics")).text)
    assert value(parsed, "testmind_oldest_job_age_seconds", status="queued") == 0
    assert value(parsed, "testmind_oldest_job_age_seconds", status="running") == 0
    assert value(parsed, "testmind_queue_messages") == 0


async def test_unreachable_dependencies_are_reported_not_fatal(
    engine: AsyncEngine, settings: Settings
) -> None:
    broken = settings.model_copy(
        update={
            "database_url": SecretStr("postgresql+asyncpg://nobody:x@127.0.0.1:1/none"),
            "redis_url": SecretStr("redis://127.0.0.1:1/0"),
        }
    )
    async for client in client_for(broken):
        answer = await client.get("/metrics")
        assert answer.status_code == 200
        parsed = samples(answer.text)
        assert value(parsed, "testmind_dependency_up", dependency="database") == 0
        assert value(parsed, "testmind_dependency_up", dependency="redis") == 0
        assert not any(key[0] in ("testmind_jobs", "testmind_queue_messages") for key in parsed)


async def test_http_requests_are_counted_by_route_template(client: httpx.AsyncClient) -> None:
    def count(route: str, status: str) -> float:
        labels = {"method": "GET", "route": route, "status": status}
        return REGISTRY.get_sample_value("testmind_http_requests_total", labels) or 0.0

    def observed(route: str) -> float:
        labels = {"method": "GET", "route": route}
        name = "testmind_http_request_duration_seconds_count"
        return REGISTRY.get_sample_value(name, labels) or 0.0

    template = "/api/v1/projects/{project_id}"
    before = (count(template, "404"), count("unmatched", "404"), observed(template))
    for _ in range(2):
        await client.get("/api/v1/projects/00000000-0000-4000-8000-000000000000")
    await client.get("/no/such/path")
    after = (count(template, "404"), count("unmatched", "404"), observed(template))
    assert after[0] - before[0] == 2
    assert after[1] - before[1] == 1
    assert after[2] - before[2] == 2
    # The exposition carries them too.
    parsed = samples((await client.get("/metrics")).text)
    assert value(parsed, "testmind_http_requests_total", method="GET", route=template,
                 status="404") == after[0]  # fmt: skip


async def test_metrics_need_the_token_when_one_is_set(
    engine: AsyncEngine, settings: Settings
) -> None:
    async for client in client_for(settings.model_copy(update={"api_token": SecretStr(TOKEN)})):
        assert (await client.get("/metrics")).status_code == 401
        allowed = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
        assert allowed.status_code == 200


def test_metrics_are_not_part_of_the_api_schema(settings: Settings) -> None:
    assert "/metrics" not in create_app(settings).openapi()["paths"]
