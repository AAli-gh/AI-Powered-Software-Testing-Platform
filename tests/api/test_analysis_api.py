import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.services.run_executor import RunExecutor

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"


@pytest.fixture
def sample_repo(repos_root: Path) -> Path:
    sample = repos_root / "sample"
    (sample / "requirements.txt").write_text("fastapi>=0.110\n")
    (sample / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    (sample / "tests").mkdir()
    (sample / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    (sample / "README.md").write_text("# Sample\n")
    return sample


async def queue_run(client: httpx.AsyncClient) -> str:
    project = (
        await client.post(PROJECTS, json={"name": "demo", "source_location": "sample"})
    ).json()
    run_id: str = (await client.post(f"{PROJECTS}/{project['id']}/runs")).json()["id"]
    return run_id


async def executed_run(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> str:
    run_id = await queue_run(client)
    # Stands in for the worker; the Celery path itself is covered in test_worker.py.
    await RunExecutor(session_factory, repos_root).execute(uuid.UUID(run_id))
    return run_id


@pytest.mark.usefixtures("sample_repo")
async def test_analysis_summary(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    run_id = await executed_run(client, session_factory, repos_root)

    response = await client.get(f"{RUNS}/{run_id}/analysis")

    assert response.status_code == 200
    body = response.json()
    assert "files" not in body
    assert body["file_count"] == 4
    assert body["primary_language"] == "Python"
    assert [(d["name"], d["confidence"]) for d in body["frameworks"]] == [("FastAPI", 1.0)]
    assert [d["name"] for d in body["test_frameworks"]] == ["pytest"]
    assert body["test_files"] == ["tests/test_main.py"]
    assert body["entry_points"] == [{"kind": "asgi_app", "path": "main.py", "target": "main:app"}]
    assert [(d["name"], d["group"]) for d in body["dependencies"]] == [("fastapi", "runtime")]


@pytest.mark.usefixtures("sample_repo")
async def test_analysis_files_are_paginated_and_filterable(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    run_id = await executed_run(client, session_factory, repos_root)
    url = f"{RUNS}/{run_id}/analysis/files"

    page = (await client.get(url, params={"limit": 2})).json()
    assert page["total"] == 4
    assert [f["path"] for f in page["items"]] == ["README.md", "main.py"]

    page = (await client.get(url, params={"limit": 2, "offset": 2})).json()
    assert [f["path"] for f in page["items"]] == ["requirements.txt", "tests/test_main.py"]

    python = (await client.get(url, params={"language": "Python"})).json()
    assert [f["path"] for f in python["items"]] == ["main.py", "tests/test_main.py"]

    tests = (await client.get(url, params={"is_test": "true"})).json()
    assert [f["path"] for f in tests["items"]] == ["tests/test_main.py"]

    non_tests = (await client.get(url, params={"is_test": "false"})).json()
    assert non_tests["total"] == 3


async def test_queued_run_has_no_analysis_yet(client: httpx.AsyncClient) -> None:
    run_id = await queue_run(client)

    response = await client.get(f"{RUNS}/{run_id}/analysis")

    assert response.status_code == 404
    assert response.json()["error"]["message"].endswith(
        "has no repository analysis (status: queued)"
    )
    assert (await client.get(f"{RUNS}/{run_id}/analysis/files")).status_code == 404


@pytest.mark.parametrize("suffix", ["analysis", "analysis/files"])
async def test_unknown_run_is_404(client: httpx.AsyncClient, suffix: str) -> None:
    response = await client.get(f"{RUNS}/{uuid.uuid4()}/{suffix}")
    assert response.status_code == 404
    assert "not found" in response.json()["error"]["message"]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 501}, {"offset": -1}, {"is_test": "x"}])
async def test_files_query_validation(
    client: httpx.AsyncClient, params: dict[str, int | str]
) -> None:
    response = await client.get(f"{RUNS}/{uuid.uuid4()}/analysis/files", params=params)
    assert response.status_code == 422
