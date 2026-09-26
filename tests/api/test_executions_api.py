import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.sandbox.models import Outcome
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.run_executor import RunExecutor
from testmind.test_engine.runner import TestSuiteRunner
from tests.doubles import RecordingDispatcher
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"


@pytest.fixture
async def run_id(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> str:
    (repos_root / "sample" / "test_main.py").write_text("def test_a(): pass\n")
    body = {"name": "demo", "source_location": "sample"}
    project = (await client.post(PROJECTS, json=body)).json()
    run: str = (await client.post(f"{PROJECTS}/{project['id']}/runs")).json()["id"]
    await RunExecutor(session_factory, repos_root).execute(uuid.UUID(run))
    return run


async def execute(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: str,
) -> str:
    execution: str = (await client.post(f"{RUNS}/{run_id}/test-executions")).json()["id"]
    sandbox = Sandbox(
        result(
            case("test_a", Outcome.PASSED, file="test_main.py"),
            case("test_b", Outcome.FAILED, file="test_main.py"),
            exit_code=1,
        )
    )
    await ExecutionExecutor(
        session_factory, repos_root, TestSuiteRunner(sandbox, Images())
    ).execute(uuid.UUID(execution))
    return execution


async def test_create_is_accepted(
    client: httpx.AsyncClient, run_id: str, dispatcher: RecordingDispatcher
) -> None:
    response = await client.post(f"{RUNS}/{run_id}/test-executions", json={"generation_id": None})
    assert response.status_code == 202
    body = response.json()
    assert (body["status"], body["outcome"], body["summary"]) == ("queued", None, None)
    assert dispatcher.executions == [uuid.UUID(body["id"])]


async def test_read_summary_tests_and_logs(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: str,
) -> None:
    execution = await execute(client, session_factory, repos_root, run_id)

    body = (await client.get(f"/api/v1/test-executions/{execution}")).json()
    assert body["status"] == "succeeded"
    assert body["outcome"] == "failed"
    summary = body["summary"]
    assert summary["totals"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
        "xfailed": 0,
    }
    assert summary["exit_meaning"] == "some tests failed"
    assert summary["image"] == "testmind-runner:abc"
    assert summary["project_test_files"] == ["test_main.py"]

    failed = (
        await client.get(f"/api/v1/test-executions/{execution}/tests", params={"outcome": "failed"})
    ).json()
    assert [t["nodeid"] for t in failed["items"]] == ["test_main.py::test_b"]
    generated = (
        await client.get(f"/api/v1/test-executions/{execution}/tests", params={"generated": "true"})
    ).json()
    assert generated["total"] == 0

    logs = (await client.get(f"/api/v1/test-executions/{execution}/logs")).json()
    assert logs == {"output": "out", "install_log": "installed"}

    listed = (await client.get(f"{RUNS}/{run_id}/test-executions")).json()
    assert [e["id"] for e in listed["items"]] == [execution]


async def test_report_endpoints_before_completion_are_404(
    client: httpx.AsyncClient, run_id: str
) -> None:
    execution = (await client.post(f"{RUNS}/{run_id}/test-executions")).json()["id"]
    for suffix in ("tests", "logs"):
        response = await client.get(f"/api/v1/test-executions/{execution}/{suffix}")
        assert response.status_code == 404
        assert "has no report (status: queued)" in response.json()["error"]["message"]


async def test_errors(client: httpx.AsyncClient, run_id: str) -> None:
    missing = uuid.uuid4()
    assert (await client.post(f"{RUNS}/{missing}/test-executions")).status_code == 404
    response = await client.post(
        f"{RUNS}/{run_id}/test-executions", json={"generation_id": str(missing)}
    )
    assert response.status_code == 404
    assert (await client.post(f"{RUNS}/{run_id}/test-executions", json={"x": 1})).status_code == 422
    assert (await client.get(f"/api/v1/test-executions/{missing}")).status_code == 404
    assert (await client.get(f"{RUNS}/{missing}/test-executions")).status_code == 404
    bad = await client.get(f"/api/v1/test-executions/{missing}/tests", params={"outcome": "great"})
    assert bad.status_code == 422
