from collections.abc import AsyncIterator

import httpx
import pytest

from testmind import __version__
from testmind.api.app import create_app
from testmind.core.config import Settings
from tests.doubles import RecordingDispatcher


async def test_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_ready_when_dependencies_are_up(client: httpx.AsyncClient) -> None:
    response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


@pytest.fixture
async def unreachable_client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """An app whose database and Redis point at a closed port."""
    broken = settings.model_copy(
        update={
            "database_url": settings.database_url.__class__(
                "postgresql+asyncpg://nobody:nothing@127.0.0.1:1/none"
            ),
            "redis_url": settings.redis_url.__class__("redis://127.0.0.1:1/0"),
        }
    )
    app = create_app(broken, dispatcher=RecordingDispatcher())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield client


async def test_ready_reports_each_unavailable_dependency(
    unreachable_client: httpx.AsyncClient,
) -> None:
    response = await unreachable_client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable", "redis": "unavailable"},
    }


async def test_liveness_does_not_depend_on_dependencies(
    unreachable_client: httpx.AsyncClient,
) -> None:
    assert (await unreachable_client.get("/health")).status_code == 200


async def test_request_id_is_generated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert len(response.headers["X-Request-ID"]) == 32


async def test_well_formed_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "trace-123.abc"})
    assert response.headers["X-Request-ID"] == "trace-123.abc"


@pytest.mark.parametrize("bad_id", ["has space", "x" * 129, "semi;colon", "<script>"])
async def test_malformed_request_id_is_replaced(client: httpx.AsyncClient, bad_id: str) -> None:
    response = await client.get("/health", headers={"X-Request-ID": bad_id})
    assert response.headers["X-Request-ID"] != bad_id
    assert len(response.headers["X-Request-ID"]) == 32


async def test_openapi_schema_is_published(client: httpx.AsyncClient) -> None:
    schema = (await client.get("/openapi.json")).json()
    assert schema["info"]["title"] == "TestMind API"
    assert {"/api/v1/projects", "/api/v1/runs/{run_id}/cancel", "/ready"} <= set(schema["paths"])
