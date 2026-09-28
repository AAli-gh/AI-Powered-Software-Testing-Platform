"""Dashboard aggregates over HTTP, computed from rows in the real database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.db.models import (
    AnalysisRun,
    GitHubPullAnalysis,
    MutationRun,
    Project,
    Regression,
    TestExecution,
)
from testmind.domain.enums import RunStatus, SourceType

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def report(passed: int, failed: int, errors: int = 0, skipped: int = 0) -> dict[str, Any]:
    total = passed + failed + errors + skipped
    return {"totals": {"total": total, "passed": passed, "failed": failed, "errors": errors,
                       "skipped": skipped, "xfailed": 0}}  # fmt: skip


async def seed(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, uuid.UUID]:
    async with session_factory() as session:
        shop = Project(
            name="shop", source_type=SourceType.GITHUB,
            source_location="github:acme/shop/" + "a" * 40,
            remote_repository="acme/shop", created_at=at(0),
        )  # fmt: skip
        idle = Project(
            name="idle", source_type=SourceType.LOCAL, source_location="sample", created_at=at(1)
        )
        session.add_all([shop, idle])
        await session.flush()
        old_run = AnalysisRun(project_id=shop.id, status=RunStatus.SUCCEEDED, created_at=at(10))
        new_run = AnalysisRun(project_id=shop.id, status=RunStatus.RUNNING, created_at=at(20))
        session.add_all([old_run, new_run])
        await session.flush()
        first = TestExecution(
            run_id=old_run.id, status=RunStatus.SUCCEEDED, outcome="failed", created_at=at(11),
            report=report(8, 1, errors=1), coverage_percent=70.0,
        )  # fmt: skip
        second = TestExecution(
            run_id=old_run.id, status=RunStatus.SUCCEEDED, outcome="passed", created_at=at(12),
            report=report(10, 0, skipped=2), coverage_percent=82.5, api_coverage_percent=50.0,
        )  # fmt: skip
        waiting = TestExecution(run_id=old_run.id, status=RunStatus.QUEUED, created_at=at(13))
        mutation = MutationRun(
            run_id=old_run.id, status=RunStatus.SUCCEEDED, options={}, created_at=at(14),
            score=0.75, report={"totals": {"total": 8, "survived": 2}},
        )  # fmt: skip
        regression = Regression(
            project_id=shop.id, status=RunStatus.SUCCEEDED, mode="full", base_ref="main~1",
            head_ref="main", base_commit="a" * 40, head_commit="b" * 40, verdict="regressions",
            created_at=at(15),
        )  # fmt: skip
        pull = GitHubPullAnalysis(
            installation_id=1, repository="acme/shop", pull_number=7, status=RunStatus.SUCCEEDED,
            conclusion="failure", head_sha="b" * 40, issues=[], created_at=at(16),
        )  # fmt: skip
        other_pull = GitHubPullAnalysis(
            installation_id=1, repository="acme/other", pull_number=1, status=RunStatus.QUEUED,
            issues=[], created_at=at(17),
        )  # fmt: skip
        session.add_all([first, second, waiting, mutation, regression, pull, other_pull])
        await session.commit()
        return {
            "shop": shop.id, "idle": idle.id, "old_run": old_run.id, "new_run": new_run.id,
            "first": first.id, "second": second.id, "waiting": waiting.id,
            "mutation": mutation.id, "regression": regression.id, "pull": pull.id,
            "other_pull": other_pull.id,
        }  # fmt: skip


async def test_platform_overview(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed(session_factory)

    body = (await client.get("/api/v1/overview")).json()

    assert body["projects"] == 2
    jobs = {j["kind"]: j["counts"] for j in body["jobs"]}
    assert jobs["analysis_runs"] == {
        "queued": 0, "running": 1, "succeeded": 1, "failed": 0, "cancelled": 0,
    }  # fmt: skip
    assert jobs["test_executions"]["succeeded"] == 2
    assert jobs["test_executions"]["queued"] == 1
    assert jobs["github_pull_analyses"]["queued"] == 1
    assert len(jobs) == 10
    assert body["active_jobs"] == 3  # the running run, the queued execution, the queued pull

    shop, idle = body["project_health"]  # latest activity first
    assert (shop["name"], idle["name"]) == ("shop", "idle")
    assert shop["latest_run"]["id"] == str(ids["new_run"])
    assert shop["latest_run"]["status"] == "running"
    assert shop["active_jobs"] == 1
    assert shop["latest_mutation_score"] == 0.75
    latest = shop["latest_execution"]
    assert latest["id"] == str(ids["second"])  # the queued one has not run
    assert latest["tests"] == {"total": 12, "passed": 10, "failed": 0, "skipped": 2}
    assert (latest["coverage_percent"], latest["api_coverage_percent"]) == (82.5, 50.0)
    assert shop["remote_repository"] == "acme/shop"
    assert (idle["latest_run"], idle["latest_execution"], idle["latest_mutation_score"]) == (
        None, None, None,
    )  # fmt: skip
    assert idle["active_jobs"] == 0

    recent = body["recent_executions"]
    assert [e["id"] for e in recent] == [str(ids[k]) for k in ("waiting", "second", "first")]
    assert recent[0]["tests"] is None
    assert recent[2]["tests"] == {"total": 10, "passed": 8, "failed": 2, "skipped": 0}
    assert recent[2]["project_name"] == "shop"
    assert [p["id"] for p in body["recent_pull_analyses"]] == [
        str(ids["other_pull"]), str(ids["pull"]),
    ]  # fmt: skip


async def test_project_overview(
    client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    ids = await seed(session_factory)

    body = (await client.get(f"/api/v1/projects/{ids['shop']}/overview")).json()

    assert body["project"]["name"] == "shop"
    assert body["runs"]["running"] == 1
    assert body["runs"]["succeeded"] == 1
    assert body["latest_run"]["id"] == str(ids["new_run"])
    assert body["latest_succeeded_run"]["id"] == str(ids["old_run"])
    # Trends are oldest first, ready to plot.
    assert [e["id"] for e in body["executions"]] == [
        str(ids[k]) for k in ("first", "second", "waiting")
    ]
    assert [e["coverage_percent"] for e in body["executions"]] == [70.0, 82.5, None]
    assert body["mutation_runs"] == [
        {
            "id": str(ids["mutation"]), "run_id": str(ids["old_run"]), "status": "succeeded",
            "created_at": "2026-09-01T00:14:00Z", "finished_at": None, "score": 0.75,
            "mutants": 8, "survived": 2,
        }
    ]  # fmt: skip
    assert [(r["verdict"], r["base_ref"], r["head_ref"]) for r in body["regressions"]] == [
        ("regressions", "main~1", "main")
    ]
    # Only the pull request analyses of the project's own repository.
    assert [p["id"] for p in body["pull_analyses"]] == [str(ids["pull"])]
    assert body["pull_analyses"][0]["conclusion"] == "failure"

    idle = (await client.get(f"/api/v1/projects/{ids['idle']}/overview")).json()
    assert idle["runs"] == {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0}
    assert (idle["latest_run"], idle["latest_succeeded_run"]) == (None, None)
    assert (idle["executions"], idle["mutation_runs"], idle["regressions"]) == ([], [], [])
    assert idle["pull_analyses"] == []

    missing = await client.get(f"/api/v1/projects/{uuid.uuid4()}/overview")
    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "not_found")


async def test_empty_platform(client: httpx.AsyncClient) -> None:
    body = (await client.get("/api/v1/overview")).json()
    assert (body["projects"], body["active_jobs"], body["project_health"]) == (0, 0, [])
    assert (body["recent_executions"], body["recent_pull_analyses"]) == ([], [])
    assert all(sum(j["counts"].values()) == 0 for j in body["jobs"])
