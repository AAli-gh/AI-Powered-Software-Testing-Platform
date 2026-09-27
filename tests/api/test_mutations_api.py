import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.doubles import RecordingDispatcher
from tests.integration.test_mutation_service import Surviving, analysed, mutation_executor


@pytest.fixture
def llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")


@pytest.mark.usefixtures("llm_configured")
async def test_lifecycle(
    client: httpx.AsyncClient,
    dispatcher: RecordingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    run_id = await analysed(session_factory, repos_root)
    created = await client.post(f"/api/v1/runs/{run_id}/mutation-runs", json={"max_mutants": 20})
    assert created.status_code == 202
    body = created.json()
    assert (body["status"], body["options"], body["summary"]) == (
        "queued",
        {"max_mutants": 20},
        None,
    )
    assert dispatcher.mutation_runs == [uuid.UUID(body["id"])]
    assert (await client.get(f"/api/v1/mutation-runs/{body['id']}/mutants")).status_code == 404

    await mutation_executor(session_factory, repos_root, Surviving()).execute(uuid.UUID(body["id"]))

    read = (await client.get(f"/api/v1/mutation-runs/{body['id']}")).json()
    assert (read["status"], read["score"]) == ("succeeded", 0.0)
    assert read["summary"]["totals"]["survived"] == 3
    survivors = await client.get(
        f"/api/v1/mutation-runs/{body['id']}/mutants",
        params={"status": "survived", "path": "app.py"},
    )
    assert survivors.json()["total"] == 3
    functions = (await client.get(f"/api/v1/mutation-runs/{body['id']}/functions")).json()
    assert [(f["function"], f["score"]) for f in functions["items"]] == [
        ("double", 0.0),
        ("untested", 0.0),
    ]
    listed = (await client.get(f"/api/v1/runs/{run_id}/mutation-runs")).json()
    assert listed["total"] == 1

    suggestion = await client.post(f"/api/v1/mutation-runs/{body['id']}/test-suggestions")
    assert suggestion.status_code == 202
    suggestion_id = suggestion.json()["id"]
    assert dispatcher.mutant_suggestions == [uuid.UUID(suggestion_id)]
    fetched = (await client.get(f"/api/v1/test-suggestions/{suggestion_id}")).json()
    assert (fetched["status"], fetched["result"]) == ("queued", None)
    listed_suggestions = (
        await client.get(f"/api/v1/mutation-runs/{body['id']}/test-suggestions")
    ).json()
    assert [s["id"] for s in listed_suggestions] == [suggestion_id]


async def test_errors(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    unknown = uuid.uuid4()
    assert (await client.post(f"/api/v1/runs/{unknown}/mutation-runs")).status_code == 404
    assert (await client.get(f"/api/v1/mutation-runs/{unknown}")).status_code == 404
    assert (await client.get(f"/api/v1/test-suggestions/{unknown}")).status_code == 404
    run_id = await analysed(session_factory, repos_root)
    bad = await client.post(f"/api/v1/runs/{run_id}/mutation-runs", json={"max_mutants": 0})
    assert bad.status_code == 422
    created = (await client.post(f"/api/v1/runs/{run_id}/mutation-runs")).json()
    no_report = await client.post(f"/api/v1/mutation-runs/{created['id']}/test-suggestions")
    assert no_report.status_code == 404
