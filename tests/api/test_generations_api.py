import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.services.run_executor import RunExecutor
from tests.doubles import RecordingDispatcher

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"


@pytest.fixture
def llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")


@pytest.fixture
def llm_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)


async def queued_run(client: httpx.AsyncClient) -> str:
    project = (
        await client.post(PROJECTS, json={"name": "demo", "source_location": "sample"})
    ).json()
    run_id: str = (await client.post(f"{PROJECTS}/{project['id']}/runs")).json()["id"]
    return run_id


@pytest.fixture
async def run_id(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> str:
    run = await queued_run(client)
    await RunExecutor(session_factory, repos_root).execute(uuid.UUID(run))
    return run


@pytest.mark.usefixtures("llm_configured")
async def test_create_and_read(
    client: httpx.AsyncClient, run_id: str, dispatcher: RecordingDispatcher
) -> None:
    body = {"kinds": ["unit", "negative"], "max_targets": 3, "path_prefix": "src/"}
    response = await client.post(f"{RUNS}/{run_id}/test-generations", json=body)

    assert response.status_code == 202
    generation = response.json()
    assert generation["status"] == "queued"
    assert generation["options"] == {**body, "coverage_execution_id": None}
    assert generation["provider"] == "anthropic"
    assert generation["result"] is None
    assert dispatcher.generations == [uuid.UUID(generation["id"])]

    fetched = await client.get(f"/api/v1/test-generations/{generation['id']}")
    assert fetched.json() == generation

    listed = (await client.get(f"{RUNS}/{run_id}/test-generations")).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == generation["id"]


@pytest.mark.usefixtures("llm_configured")
async def test_default_options(client: httpx.AsyncClient, run_id: str) -> None:
    generation = (await client.post(f"{RUNS}/{run_id}/test-generations")).json()
    assert generation["options"] == {
        "kinds": ["unit", "edge_cases", "negative"],
        "max_targets": None,
        "path_prefix": None,
        "coverage_execution_id": None,
    }


@pytest.mark.usefixtures("llm_missing")
async def test_without_llm_is_503(client: httpx.AsyncClient, run_id: str) -> None:
    response = await client.post(f"{RUNS}/{run_id}/test-generations")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


@pytest.mark.usefixtures("llm_configured")
async def test_run_not_succeeded_is_409(client: httpx.AsyncClient) -> None:
    run = await queued_run(client)
    response = await client.post(f"{RUNS}/{run}/test-generations")
    assert response.status_code == 409


@pytest.mark.usefixtures("llm_configured")
@pytest.mark.parametrize(
    "body",
    [
        {"kinds": []},
        {"kinds": ["fuzz"]},
        {"max_targets": 0},
        {"max_targets": 201},
        {"unexpected": 1},
    ],
)
async def test_invalid_options_are_422(
    client: httpx.AsyncClient, run_id: str, body: dict[str, object]
) -> None:
    assert (await client.post(f"{RUNS}/{run_id}/test-generations", json=body)).status_code == 422


async def test_unknown_ids_are_404(client: httpx.AsyncClient) -> None:
    missing = uuid.uuid4()
    assert (await client.get(f"/api/v1/test-generations/{missing}")).status_code == 404
    assert (await client.get(f"{RUNS}/{missing}/test-generations")).status_code == 404
