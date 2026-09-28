"""API token authentication and rate limiting, over HTTP with the real Redis."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from testmind.api.app import create_app
from testmind.core.config import Settings
from tests.conftest import TEST_REDIS_URL
from tests.doubles import RecordingDispatcher

TOKEN = "t" * 40


async def client_for(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, dispatcher=RecordingDispatcher())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client


@pytest.fixture
async def secured(engine: AsyncEngine, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    async for client in client_for(settings.model_copy(update={"api_token": SecretStr(TOKEN)})):
        yield client


@pytest.fixture
async def limited(engine: AsyncEngine, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    redis = Redis.from_url(TEST_REDIS_URL)
    for key in await redis.keys("testmind:ratelimit:*"):
        await redis.delete(key)
    await redis.aclose()
    async for client in client_for(settings.model_copy(update={"rate_limit_per_minute": 3})):
        yield client


async def test_the_api_needs_the_token(secured: httpx.AsyncClient) -> None:
    refused: list[dict[str, str]] = [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": "Bearer"},
    ]
    for headers in refused:
        answer = await secured.get("/api/v1/projects", headers=headers)
        assert answer.status_code == 401
        assert answer.json()["error"]["code"] == "unauthorized"
        assert answer.headers["WWW-Authenticate"] == 'Bearer realm="testmind"'
        assert "X-Request-ID" in answer.headers
    ok = await secured.get("/api/v1/projects", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    lower = await secured.get("/api/v1/overview", headers={"authorization": f"bearer {TOKEN}"})
    assert lower.status_code == 200


async def test_probes_docs_and_the_signed_webhook_stay_open(secured: httpx.AsyncClient) -> None:
    assert (await secured.get("/health")).status_code == 200
    assert (await secured.get("/openapi.json")).status_code == 200
    webhook = await secured.post(
        "/api/v1/github/webhook", content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d"},
    )  # fmt: skip
    # Not 401: the webhook authenticates with its own signature (here: not configured).
    assert webhook.status_code == 503


async def test_changes_are_rate_limited_per_client(limited: httpx.AsyncClient) -> None:
    body = {"name": "x", "source_location": "sample"}
    statuses = [
        (await limited.post("/api/v1/projects", json={**body, "name": f"p{i}"})).status_code
        for i in range(4)
    ]
    assert statuses == [201, 201, 201, 429]
    refused = await limited.post("/api/v1/projects", json={**body, "name": "p9"})
    assert refused.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(refused.headers["Retry-After"]) <= 60
    # Reading is not limited, nor are GitHub's deliveries.
    assert (await limited.get("/api/v1/projects")).status_code == 200
    webhook = await limited.post(
        "/api/v1/github/webhook", content=b"{}",
        headers={"X-GitHub-Event": "ping", "X-GitHub-Delivery": "d"},
    )  # fmt: skip
    assert webhook.status_code == 503


async def test_rate_limiting_lets_requests_through_without_redis(
    engine: AsyncEngine, settings: Settings
) -> None:
    unreachable = settings.model_copy(
        update={"rate_limit_per_minute": 1, "redis_url": SecretStr("redis://127.0.0.1:1/0")}
    )
    async for client in client_for(unreachable):
        for name in ("a", "b", "c"):
            answer = await client.post(
                "/api/v1/projects", json={"name": name, "source_location": "sample"}
            )
            assert answer.status_code == 201
