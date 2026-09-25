from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI


async def test_unknown_route_uses_error_format(client: httpx.AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"error": {"code": "not_found", "message": "Not Found"}}


async def test_method_not_allowed_uses_error_format(client: httpx.AsyncClient) -> None:
    response = await client.put("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["allow"]


@pytest.fixture
async def crashing_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    # Starlette re-raises unhandled errors after responding; keep the response instead.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        yield client


async def test_unhandled_exception_returns_generic_500(crashing_client: httpx.AsyncClient) -> None:
    response = await crashing_client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": "Internal server error"}
    }
    assert "secret" not in response.text
