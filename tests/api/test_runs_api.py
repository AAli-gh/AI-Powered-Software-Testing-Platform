import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from testmind.api.app import create_app
from testmind.core.config import Settings
from tests.doubles import FailingDispatcher, RecordingDispatcher

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"


async def create_project(client: httpx.AsyncClient) -> str:
    response = await client.post(PROJECTS, json={"name": "demo", "source_location": "sample"})
    project_id: str = response.json()["id"]
    return project_id


async def test_create_run_is_accepted_and_dispatched(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher
) -> None:
    project_id = await create_project(client)

    response = await client.post(f"{PROJECTS}/{project_id}/runs")

    assert response.status_code == 202
    run = response.json()
    assert run["status"] == "queued"
    assert run["project_id"] == project_id
    assert run["started_at"] is None
    assert dispatcher.dispatched == [uuid.UUID(run["id"])]


async def test_create_run_for_missing_project_is_404(
    client: httpx.AsyncClient, dispatcher: RecordingDispatcher
) -> None:
    response = await client.post(f"{PROJECTS}/{uuid.uuid4()}/runs")
    assert response.status_code == 404
    assert dispatcher.dispatched == []


@pytest.fixture
async def broker_down_client(app: FastAPI, settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    failing = create_app(settings, dispatcher=FailingDispatcher())
    async with failing.router.lifespan_context(failing):
        transport = httpx.ASGITransport(app=failing)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield client


async def test_dispatch_failure_is_503_and_run_is_failed(
    broker_down_client: httpx.AsyncClient,
) -> None:
    project_id = await create_project(broker_down_client)

    response = await broker_down_client.post(f"{PROJECTS}/{project_id}/runs")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dispatch_failed"
    runs = (await broker_down_client.get(f"{PROJECTS}/{project_id}/runs")).json()
    assert [run["status"] for run in runs["items"]] == ["failed"]


async def test_get_run(client: httpx.AsyncClient) -> None:
    project_id = await create_project(client)
    created = (await client.post(f"{PROJECTS}/{project_id}/runs")).json()

    response = await client.get(f"{RUNS}/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


async def test_get_missing_run_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{RUNS}/{uuid.uuid4()}")).status_code == 404


async def test_list_runs(client: httpx.AsyncClient) -> None:
    project_id = await create_project(client)
    for _ in range(3):
        await client.post(f"{PROJECTS}/{project_id}/runs")

    page = (await client.get(f"{PROJECTS}/{project_id}/runs", params={"limit": 2})).json()

    assert page["total"] == 3
    assert len(page["items"]) == 2


async def test_list_runs_for_missing_project_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{PROJECTS}/{uuid.uuid4()}/runs")).status_code == 404


async def test_cancel_queued_run(client: httpx.AsyncClient) -> None:
    project_id = await create_project(client)
    run = (await client.post(f"{PROJECTS}/{project_id}/runs")).json()

    response = await client.post(f"{RUNS}/{run['id']}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["finished_at"] is not None


async def test_cancel_twice_is_409(client: httpx.AsyncClient) -> None:
    project_id = await create_project(client)
    run = (await client.post(f"{PROJECTS}/{project_id}/runs")).json()
    await client.post(f"{RUNS}/{run['id']}/cancel")

    response = await client.post(f"{RUNS}/{run['id']}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"


async def test_cancel_missing_run_is_404(client: httpx.AsyncClient) -> None:
    assert (await client.post(f"{RUNS}/{uuid.uuid4()}/cancel")).status_code == 404


async def test_project_can_be_deleted_after_cancel(client: httpx.AsyncClient) -> None:
    project_id = await create_project(client)
    run = (await client.post(f"{PROJECTS}/{project_id}/runs")).json()
    await client.post(f"{RUNS}/{run['id']}/cancel")

    assert (await client.delete(f"{PROJECTS}/{project_id}")).status_code == 204
