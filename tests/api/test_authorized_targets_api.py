"""Authorized targets over HTTP: register a host, publish the token, verify ownership."""

from __future__ import annotations

import uuid

import httpx
import pytest

from testmind.web_engine.ownership import WELL_KNOWN_PATH


async def test_register_issues_a_token_and_instructions(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/authorized-targets", json={"host": "Example.com"})
    assert response.status_code == 201
    body = response.json()
    assert body["host"] == "example.com"  # normalized
    assert body["token"].startswith("testmind-site-verification=")
    assert body["verified"] is False
    assert WELL_KNOWN_PATH in body["instructions"]


async def test_registering_an_internal_host_is_refused(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/authorized-targets", json={"host": "127.0.0.1"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_target"


async def test_registering_the_same_host_twice_keeps_the_token(client: httpx.AsyncClient) -> None:
    first = (await client.post("/api/v1/authorized-targets", json={"host": "example.com"})).json()
    again = (await client.post("/api/v1/authorized-targets", json={"host": "example.com"})).json()
    assert again["id"] == first["id"]
    assert again["token"] == first["token"]


async def test_verify_succeeds_when_the_token_is_published(
    client: httpx.AsyncClient, prober_responses: dict[str, str]
) -> None:
    created = (await client.post("/api/v1/authorized-targets", json={"host": "example.com"})).json()
    # The owner publishes the token at the well-known path (https is tried first).
    prober_responses[f"https://example.com{WELL_KNOWN_PATH}"] = f"{created['token']}\n"

    verified = (await client.post(f"/api/v1/authorized-targets/{created['id']}/verify")).json()
    assert verified["verified"] is True
    assert verified["verified_at"] is not None
    assert verified["last_error"] is None

    listing = (await client.get("/api/v1/authorized-targets")).json()
    assert listing[0]["verified"] is True


async def test_verify_fails_when_the_token_is_absent(
    client: httpx.AsyncClient, prober_responses: dict[str, str]
) -> None:
    created = (await client.post("/api/v1/authorized-targets", json={"host": "example.com"})).json()
    prober_responses[f"https://example.com{WELL_KNOWN_PATH}"] = "nothing useful here"

    verified = (await client.post(f"/api/v1/authorized-targets/{created['id']}/verify")).json()
    assert verified["verified"] is False
    assert verified["last_error"]


@pytest.mark.parametrize("missing", ["get", "verify"])
async def test_unknown_target_reads_404(client: httpx.AsyncClient, missing: str) -> None:
    target_id = uuid.uuid4()
    if missing == "get":
        response = await client.get(f"/api/v1/authorized-targets/{target_id}")
    else:
        response = await client.post(f"/api/v1/authorized-targets/{target_id}/verify")
    assert response.status_code == 404
