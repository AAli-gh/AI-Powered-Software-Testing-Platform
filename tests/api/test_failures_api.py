import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.services.failure_analysis_executor import FailureAnalysisExecutor
from tests.doubles import RecordingDispatcher, ScriptedLLM
from tests.integration.test_failure_analysis import REPLY, failed_execution

EXECUTIONS = "/api/v1/test-executions"


@pytest.fixture
def llm_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")


@pytest.fixture
async def execution(
    session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> uuid.UUID:
    return await failed_execution(session_factory, repos_root)


async def test_overview_and_groups(client: httpx.AsyncClient, execution: uuid.UUID) -> None:
    overview = (await client.get(f"{EXECUTIONS}/{execution}/failures")).json()
    assert overview == {
        "failed_tests": 1,
        "analysed_tests": 1,
        "unanalysed_tests": [],
        "notes": [],
        "groups": 1,
        "by_category": {"exception_in_code": 1},
        "decided": 0,
    }

    groups = (await client.get(f"{EXECUTIONS}/{execution}/failures/groups")).json()
    assert groups["total"] == 1
    [group] = groups["items"]
    assert group["crash"]["path"] == "main.py"
    assert group["tests"][0]["nodeid"] == "test_main.py::test_ratio"
    filtered = await client.get(
        f"{EXECUTIONS}/{execution}/failures/groups", params={"category": "import_error"}
    )
    assert filtered.json()["total"] == 0

    one = await client.get(f"{EXECUTIONS}/{execution}/failures/groups/{group['id']}")
    assert one.json() == group
    missing = await client.get(f"{EXECUTIONS}/{execution}/failures/groups/nope")
    assert missing.status_code == 404
    assert "Failure group 'nope' not found" in missing.json()["error"]["message"]

    summary = (await client.get(f"{EXECUTIONS}/{execution}")).json()["summary"]
    assert summary["failure_groups"] == 1


async def test_unknown_execution(client: httpx.AsyncClient) -> None:
    response = await client.get(f"{EXECUTIONS}/{uuid.uuid4()}/failures")
    assert response.status_code == 404
    assert (await client.get(f"{EXECUTIONS}/x/failures")).status_code == 422


async def test_ai_analysis_without_llm_is_503(
    client: httpx.AsyncClient, execution: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    response = await client.post(f"{EXECUTIONS}/{execution}/failure-analyses")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


@pytest.mark.usefixtures("llm_configured")
async def test_ai_analysis_lifecycle(
    client: httpx.AsyncClient,
    execution: uuid.UUID,
    dispatcher: RecordingDispatcher,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> None:
    created = await client.post(f"{EXECUTIONS}/{execution}/failure-analyses")
    assert created.status_code == 202
    body = created.json()
    assert (body["status"], body["result"], body["provider"]) == ("queued", None, "anthropic")
    assert dispatcher.failure_analyses == [uuid.UUID(body["id"])]

    executor = FailureAnalysisExecutor(
        session_factory, repos_root, provider_factory=lambda: ScriptedLLM([REPLY]),
        max_tokens=100, max_groups=3,
    )  # fmt: skip
    await executor.execute(uuid.UUID(body["id"]))

    fetched = (await client.get(f"/api/v1/failure-analyses/{body['id']}")).json()
    assert fetched["status"] == "succeeded"
    [hypothesis] = fetched["result"]["hypotheses"]
    assert (hypothesis["blame"], hypothesis["confidence"]) == ("code", 0.8)
    assert hypothesis["evidence"][0]["verified"] is True

    listed = (await client.get(f"{EXECUTIONS}/{execution}/failure-analyses")).json()
    assert [a["id"] for a in listed["items"]] == [body["id"]]
    assert (await client.get(f"/api/v1/failure-analyses/{uuid.uuid4()}")).status_code == 404
