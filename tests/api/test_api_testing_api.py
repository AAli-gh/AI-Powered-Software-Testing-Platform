import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.api_engine.probes import PROBE_FILE
from tests.doubles import RecordingDispatcher
from tests.integration.test_api_testing_service import PROJECT_CALL, analysed, executed


@pytest.fixture
def llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")


async def test_discovered_endpoints(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> None:
    run_id = await analysed(session_factory, repos_root)
    body = (await client.get(f"/api/v1/runs/{run_id}/api")).json()
    assert [(a["framework"], a["variable"]) for a in body["apps"]] == [("flask", "app")]
    assert [(e["method"], e["path"], e["params"]) for e in body["endpoints"]] == [
        ("GET", "/items", []),
        ("DELETE", "/items/{item_id}", [{"name": "item_id", "type": "int"}]),
    ]
    assert body["notes"] == []
    assert (await client.get(f"/api/v1/runs/{uuid.uuid4()}/api")).status_code == 404


async def test_execution_with_probes_and_its_api_coverage(
    client: httpx.AsyncClient,
    dispatcher: RecordingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    run_id = await analysed(session_factory, repos_root)
    created = await client.post(f"/api/v1/runs/{run_id}/test-executions", json={"api_probes": True})
    assert created.status_code == 202
    assert created.json()["api_probes"] is True
    pending = await client.get(f"/api/v1/test-executions/{created.json()['id']}/api-coverage")
    assert pending.status_code == 404
    assert "has not finished" in pending.json()["error"]["message"]

    probe = {
        **PROJECT_CALL, "test_path": f"/workspace/tests/{PROBE_FILE}",
        "test_name": "test_probe_001_unknown_path", "route": None, "status": 404,
    }  # fmt: skip
    typo = {**PROJECT_CALL, "route": None, "path": "/itmes", "status": 404}
    execution_id, _ = await executed(
        session_factory, repos_root, run_id, PROJECT_CALL, probe, typo, api_probes=True
    )
    base = f"/api/v1/test-executions/{execution_id}"
    assert (await client.get(base)).json()["summary"]["api_coverage_percent"] == 50.0
    overview = (await client.get(f"{base}/api-coverage")).json()
    assert overview["totals"] == {
        "endpoints": 2, "tested": 1, "success_tested": 1, "error_tested": 0, "percent": 50.0,
    }  # fmt: skip
    assert (overview["gap_count"], overview["unmatched_requests"]) == (1, 1)
    assert overview["probes"]["passed"] == 1
    assert overview["top_gaps"][0]["kind"] == "untested"

    untested = (await client.get(f"{base}/api-coverage/endpoints", params={"tested": False})).json()
    assert [e["path"] for e in untested["items"]] == ["/items/{item_id}"]
    filtered = await client.get(
        f"{base}/api-coverage/endpoints", params={"method": "get", "source": "static"}
    )
    assert [e["path"] for e in filtered.json()["items"]] == ["/items"]
    gaps = (await client.get(f"{base}/api-coverage/gaps", params={"kind": "untested"})).json()
    assert gaps["total"] == 1
    bad_kind = await client.get(f"{base}/api-coverage/gaps", params={"kind": "nonsense"})
    assert bad_kind.status_code == 422
    passed = await client.get(f"{base}/api-coverage/probes", params={"verdict": "passed"})
    assert [p["test"] for p in passed.json()["items"]] == ["test_probe_001_unknown_path"]
    unmatched = (await client.get(f"{base}/api-coverage/unmatched")).json()
    assert [(u["path"], u["status"]) for u in unmatched["items"]] == [("/itmes", 404)]


@pytest.mark.usefixtures("llm_configured")
async def test_api_test_suggestions(
    client: httpx.AsyncClient,
    dispatcher: RecordingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    run_id = await analysed(session_factory, repos_root)
    execution_id, _ = await executed(session_factory, repos_root, run_id, PROJECT_CALL)
    created = await client.post(f"/api/v1/test-executions/{execution_id}/api-test-suggestions")
    assert created.status_code == 202
    body = created.json()
    assert (body["status"], body["result"], body["execution_id"]) == (
        "queued", None, str(execution_id),
    )  # fmt: skip
    assert dispatcher.api_suggestions == [uuid.UUID(body["id"])]
    fetched = (await client.get(f"/api/v1/api-test-suggestions/{body['id']}")).json()
    assert fetched["id"] == body["id"]
    listed = await client.get(f"/api/v1/test-executions/{execution_id}/api-test-suggestions")
    assert [s["id"] for s in listed.json()] == [body["id"]]
    unknown = uuid.uuid4()
    assert (await client.get(f"/api/v1/api-test-suggestions/{unknown}")).status_code == 404
    missing = await client.post(f"/api/v1/test-executions/{unknown}/api-test-suggestions")
    assert missing.status_code == 404
