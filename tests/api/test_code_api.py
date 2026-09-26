import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.analyzers.code.rules import RULES
from testmind.services.run_executor import RunExecutor

PROJECTS = "/api/v1/projects"
RUNS = "/api/v1/runs"

SERVICE = """
import pickle
import os


def average(total, count):
    return total / count


def load(blob):
    return pickle.loads(blob)


def grade(score):
    if score > 90:
        return "A"
    elif score > 80:
        return "B"
    elif score > 70:
        return "C"
    elif score > 60:
        return "D"
    elif score > 50:
        return "E"
    elif score > 40:
        return "F"
    elif score > 30:
        return "G"
    elif score > 20:
        return "H"
    elif score > 10:
        return "I"
    elif score > 5:
        return "J"
    return "K"


class Cart:
    def total(self, items=[]):
        return sum(items)
"""


@pytest.fixture
async def run_id(
    client: httpx.AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    repos_root: Path,
) -> str:
    sample = repos_root / "sample"
    (sample / "shop").mkdir()
    (sample / "shop" / "service.py").write_text(SERVICE)
    (sample / "tests").mkdir()
    (sample / "tests" / "test_service.py").write_text("def test_x():\n    assert eval('1')\n")
    body = {"name": "demo", "source_location": "sample"}
    project = (await client.post(PROJECTS, json=body)).json()
    run: str = (await client.post(f"{PROJECTS}/{project['id']}/runs")).json()["id"]
    await RunExecutor(session_factory, repos_root).execute(uuid.UUID(run))
    return run


async def test_code_summary(client: httpx.AsyncClient, run_id: str) -> None:
    body = (await client.get(f"{RUNS}/{run_id}/code")).json()
    assert set(body) == {"metrics", "issues"}
    metrics = body["metrics"]
    assert metrics["modules"] == 2  # main.py + shop/service.py
    assert metrics["test_modules_skipped"] == 1
    assert (metrics["functions"], metrics["methods"], metrics["classes"]) == (3, 1, 1)
    assert metrics["max_complexity"] == 11
    assert metrics["findings_by_severity"] == {"high": 1, "low": 1, "medium": 3}


async def test_findings_sorted_and_filtered(client: httpx.AsyncClient, run_id: str) -> None:
    url = f"{RUNS}/{run_id}/code/findings"
    all_findings = (await client.get(url)).json()
    assert [f["rule_id"] for f in all_findings["items"]] == [
        "TM011",
        "TM040",
        "TM030",
        "TM020",
        "TM023",
    ]
    assert all_findings["total"] == 5

    high = (await client.get(url, params={"min_severity": "high"})).json()
    assert [f["rule_id"] for f in high["items"]] == ["TM011"]

    dead = (await client.get(url, params={"category": "dead_code"})).json()
    assert [f["message"] for f in dead["items"]] == ["'os' is imported but never used"]

    confident = (await client.get(url, params={"min_confidence": 0.95})).json()
    assert {f["rule_id"] for f in confident["items"]} == {"TM011", "TM030", "TM020"}

    by_rule = (await client.get(url, params={"rule_id": "TM040", "path_prefix": "shop/"})).json()
    [finding] = by_rule["items"]
    assert finding["symbol"] == "average"
    assert finding["evidence"] == "return total / count"
    assert (await client.get(url, params={"path_prefix": "nope/"})).json()["total"] == 0


async def test_functions(client: httpx.AsyncClient, run_id: str) -> None:
    url = f"{RUNS}/{run_id}/code/functions"
    by_path = (await client.get(url)).json()
    assert [f["qualname"] for f in by_path["items"]] == ["average", "load", "grade", "Cart.total"]

    complex_first = (await client.get(url, params={"sort": "complexity", "limit": 1})).json()
    assert complex_first["items"][0]["qualname"] == "grade"
    assert complex_first["items"][0]["complexity_rank"] == "C"
    assert complex_first["total"] == 4

    filtered = (await client.get(url, params={"min_complexity": 2, "path_prefix": "shop"})).json()
    assert [f["qualname"] for f in filtered["items"]] == ["grade"]

    average = by_path["items"][0]
    assert [p["name"] for p in average["parameters"]] == ["total", "count"]


async def test_modules_and_classes(client: httpx.AsyncClient, run_id: str) -> None:
    modules = (await client.get(f"{RUNS}/{run_id}/code/modules")).json()
    assert [m["module"] for m in modules["items"]] == ["main", "shop.service"]
    only_shop = (
        await client.get(f"{RUNS}/{run_id}/code/modules", params={"path_prefix": "shop"})
    ).json()
    assert only_shop["total"] == 1
    classes = (await client.get(f"{RUNS}/{run_id}/code/classes")).json()
    assert [(c["qualname"], c["methods"]) for c in classes["items"]] == [("Cart", ["total"])]
    none = (await client.get(f"{RUNS}/{run_id}/code/classes", params={"path_prefix": "x"})).json()
    assert none["items"] == []


async def test_rule_catalog(client: httpx.AsyncClient) -> None:
    rules = (await client.get("/api/v1/code-rules")).json()
    assert [r["id"] for r in rules] == list(RULES)
    assert {"id": "TM010", "category": "dangerous_pattern", "title": "eval / exec"} in rules


@pytest.mark.parametrize(
    "suffix", ["code", "code/modules", "code/functions", "code/classes", "code/findings"]
)
async def test_missing_run_or_analysis_is_404(client: httpx.AsyncClient, suffix: str) -> None:
    assert (await client.get(f"{RUNS}/{uuid.uuid4()}/{suffix}")).status_code == 404
    body = {"name": f"p-{suffix.replace('/', '-')}", "source_location": "sample"}
    project = (await client.post(PROJECTS, json=body)).json()
    queued = (await client.post(f"{PROJECTS}/{project['id']}/runs")).json()["id"]
    response = await client.get(f"{RUNS}/{queued}/{suffix}")
    assert response.status_code == 404
    assert "has no code analysis (status: queued)" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("suffix", "params"),
    [
        ("code/findings", {"min_severity": "urgent"}),
        ("code/findings", {"rule_id": "X1"}),
        ("code/findings", {"min_confidence": 2}),
        ("code/findings", {"category": "style"}),
        ("code/functions", {"sort": "name"}),
        ("code/functions", {"min_complexity": 0}),
        ("code/modules", {"limit": 501}),
    ],
)
async def test_query_validation(
    client: httpx.AsyncClient, suffix: str, params: dict[str, str | int]
) -> None:
    response = await client.get(f"{RUNS}/{uuid.uuid4()}/{suffix}", params=params)
    assert response.status_code == 422
