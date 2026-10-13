"""Website scans over HTTP, against the real database (the crawl itself is not run here)."""

from __future__ import annotations

import uuid

import httpx

from tests.doubles import RecordingDispatcher


async def test_create_lists_and_reads_a_scan(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher
) -> None:
    response = await client.post(
        "/api/v1/web-scans",
        json={"target_url": "https://example.com/", "max_pages": 20, "interact": True},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["target_url"] == "https://example.com/"
    assert body["interact"] is True
    assert body["result"] is None
    scan_id = body["id"]
    assert dispatcher.web_scans == [uuid.UUID(scan_id)]

    got = await client.get(f"/api/v1/web-scans/{scan_id}")
    assert got.status_code == 200
    assert got.json()["id"] == scan_id

    listing = await client.get("/api/v1/web-scans")
    assert listing.status_code == 200
    page = listing.json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == scan_id


async def test_an_internal_target_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/web-scans", json={"target_url": "http://127.0.0.1:8000/admin"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_target"


async def test_an_unknown_scan_is_404(client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/v1/web-scans/{uuid.uuid4()}")
    assert response.status_code == 404
