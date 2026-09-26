import json
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.sandbox.models import Outcome
from testmind.services.execution_executor import ExecutionExecutor
from testmind.services.run_executor import RunExecutor
from testmind.test_engine.runner import TestSuiteRunner
from tests.unit.test_engine.test_runner import Images, Sandbox, case, result

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"
EXECUTIONS = "/api/v1/test-executions"

MAIN = """\
def covered(x):
    if x:
        return 1
    return 0


def uncovered():
    return 2
"""


def coverage_json(
    executed: list[int], missing: list[int], missing_branches: list[list[int]]
) -> str:
    branches = [[2, 3], [2, 4]]
    return json.dumps(
        {
            "meta": {"version": "7.16.1", "branch_coverage": True},
            "files": {
                "main.py": {
                    "executed_lines": executed,
                    "missing_lines": missing,
                    "executed_branches": [b for b in branches if b not in missing_branches],
                    "missing_branches": missing_branches,
                },
                "test_main.py": {"executed_lines": [1], "missing_lines": []},
            },
        }
    )


BASE = coverage_json([1, 2, 4, 7], [3, 8], [[2, 3]])
FULL = coverage_json([1, 2, 3, 4, 7, 8], [], [])


@pytest.fixture
async def run_id(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], repos_root: Path
) -> str:
    (repos_root / "sample" / "main.py").write_text(MAIN)
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
    coverage: str | None,
) -> str:
    execution: str = (await client.post(f"{RUNS}/{run_id}/test-executions")).json()["id"]
    sandbox = Sandbox(result(case("test_a", Outcome.PASSED), coverage_json=coverage))
    await ExecutionExecutor(
        session_factory, repos_root, TestSuiteRunner(sandbox, Images())
    ).execute(uuid.UUID(execution))
    return execution


@pytest.fixture
async def execution(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: str,
) -> str:
    return await execute(client, session_factory, repos_root, run_id, BASE)


async def test_overview(client: httpx.AsyncClient, execution: str) -> None:
    body = (await client.get(f"{EXECUTIONS}/{execution}/coverage")).json()
    assert body["tool"] == "coverage.py 7.16.1"
    assert body["threshold"] == 80.0
    totals = body["totals"]
    assert (totals["statements"], totals["covered_lines"]) == (6, 4)
    assert (totals["branches"], totals["covered_branches"]) == (2, 1)
    assert totals["percent"] == 62.5  # (4 + 1) / (6 + 2)
    assert totals["functions_never_executed"] == 1
    assert body["unmeasured_files"] == []
    assert body["gap_count"] == 2
    # uncovered: 1.0 x 1 = 1.0; covered: 0.4 x 2 = 0.8
    assert [(g["qualname"], g["risk"]) for g in body["top_gaps"]] == [
        ("uncovered", 1.0),
        ("covered", 0.8),
    ]

    summary = (await client.get(f"{EXECUTIONS}/{execution}")).json()["summary"]
    assert (summary["coverage_percent"], summary["coverage_error"]) == (62.5, None)


async def test_files(client: httpx.AsyncClient, execution: str) -> None:
    body = (await client.get(f"{EXECUTIONS}/{execution}/coverage/files")).json()
    assert body["total"] == 1  # the test module is not production code
    [file] = body["items"]
    assert (file["path"], file["missing"], file["missing_branches_detail"]) == (
        "main.py",
        ["3", "8"],
        ["2->3"],
    )
    url = f"{EXECUTIONS}/{execution}/coverage/files"
    assert (await client.get(url, params={"below": 50})).json()["total"] == 0
    by_percent = (await client.get(url, params={"sort": "percent", "below": 100})).json()
    assert [f["path"] for f in by_percent["items"]] == ["main.py"]


async def test_functions(client: httpx.AsyncClient, execution: str) -> None:
    url = f"{EXECUTIONS}/{execution}/coverage/functions"
    by_path = (await client.get(url, params={"path": "main.py"})).json()
    assert [f["qualname"] for f in by_path["items"]] == ["covered", "uncovered"]
    by_percent = (await client.get(url, params={"sort": "percent"})).json()
    assert [(f["qualname"], f["percent"]) for f in by_percent["items"]] == [
        ("uncovered", 0.0),
        ("covered", 60.0),
    ]
    below = (await client.get(url, params={"below": 50})).json()
    assert [f["qualname"] for f in below["items"]] == ["uncovered"]
    assert (await client.get(url, params={"path": "other.py"})).json()["total"] == 0
    assert (await client.get(url, params={"below": 101})).status_code == 422


async def test_gaps_are_paginated(client: httpx.AsyncClient, execution: str) -> None:
    body = (await client.get(f"{EXECUTIONS}/{execution}/coverage/gaps", params={"limit": 1})).json()
    assert body["total"] == 2
    [gap] = body["items"]
    assert gap["qualname"] == "uncovered"
    assert gap["reasons"] == ["never executed by the test suite"]


async def test_compare(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: str,
    execution: str,
) -> None:
    improved = await execute(client, session_factory, repos_root, run_id, FULL)
    response = await client.get(
        f"{EXECUTIONS}/{improved}/coverage/compare", params={"base": execution}
    )
    assert response.status_code == 200
    diff = response.json()
    assert (diff["base_percent"], diff["head_percent"], diff["delta"]) == (62.5, 100.0, 37.5)
    assert (diff["newly_covered_lines"], diff["newly_missed_lines"]) == (2, 0)
    assert [(f["qualname"], f["delta"]) for f in diff["functions"]] == [
        ("covered", 40.0),
        ("uncovered", 100.0),
    ]


async def test_missing_coverage_is_a_404_with_the_reason(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
    run_id: str,
    execution: str,
) -> None:
    without = await execute(client, session_factory, repos_root, run_id, None)
    for url in ("", "/files", "/functions", "/gaps"):
        response = await client.get(f"{EXECUTIONS}/{without}/coverage{url}")
        assert response.status_code == 404
        assert "coverage.py wrote no report" in response.json()["error"]["message"]

    unknown = uuid.uuid4()
    assert (await client.get(f"{EXECUTIONS}/{unknown}/coverage")).status_code == 404
    compare = await client.get(
        f"{EXECUTIONS}/{execution}/coverage/compare", params={"base": str(without)}
    )
    assert compare.status_code == 404
    summary = (await client.get(f"{EXECUTIONS}/{without}")).json()["summary"]
    assert summary["coverage_percent"] is None
    assert "coverage.py wrote no report" in summary["coverage_error"]
