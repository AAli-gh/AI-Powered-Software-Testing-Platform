"""Load test for TestMind's read API (docs/performance.md).

    # 1. Seed finished jobs with realistic reports into the stack's database (run inside the
    #    API image, which has TestMind and the database URL):
    docker compose run --rm --no-deps -v "$PWD/scripts:/scripts:ro" api \\
        python /scripts/load_test.py seed --projects 50 --runs 20
    # 2. Measure, from the host:
    uv run python scripts/load_test.py run --url http://127.0.0.1:8000 --requests 300

`run` discovers ids through the API, then sends `--requests` GETs per endpoint with
`--concurrency` in flight, and prints latency percentiles, throughput and errors per endpoint
as a Markdown table. Only reads are measured: they are what the dashboard does all day, and
writes are rate-limited by design.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any

import httpx

# ------------------------------------------------------------------------------------ seed
FAILURE_RATE = 0.03  # share of failed tests in a seeded report


def _report(tests: int, rng: random.Random) -> dict[str, Any]:
    from testmind.sandbox.models import Outcome as TestOutcome  # noqa: PLC0415
    from testmind.test_engine.models import (  # noqa: PLC0415
        ExecutionOutcome,
        InstallReport,
        TestCaseReport,
        TestRunReport,
        TestTotals,
    )

    cases = []
    failed = 0
    for i in range(tests):
        fails = rng.random() < FAILURE_RATE
        failed += fails
        cases.append(
            TestCaseReport(
                nodeid=f"tests/test_module_{i // 20}.py::test_case_{i}",
                file=f"tests/test_module_{i // 20}.py",
                line=10 + (i % 20) * 7,
                name=f"test_case_{i}",
                outcome=TestOutcome.FAILED if fails else TestOutcome.PASSED,
                duration_seconds=round(rng.random() / 10, 4),
                message="AssertionError: assert 41 == 42" if fails else None,
                traceback=("Traceback (most recent call last):\n" + "  frame\n" * 30)
                if fails
                else None,
                generated=False,
            )
        )
    report = TestRunReport(
        outcome=ExecutionOutcome.FAILED if failed else ExecutionOutcome.PASSED,
        exit_code=1 if failed else 0,
        exit_meaning="tests failed" if failed else "all tests passed",
        totals=TestTotals(total=tests, passed=tests - failed, failed=failed),
        duration_seconds=round(tests * 0.05, 2),
        tests=cases,
        output="." * 4000,
        install=InstallReport(
            image="testmind-runner:0123456789abcdef",
            requirements=["requests==2.32.3"],
            from_cache=True,
            notes=[],
            log="Successfully installed requests-2.32.3\n" * 20,
        ),
        project_test_files=[f"tests/test_module_{i}.py" for i in range(tests // 20)],
        generated_test_files=[],
        environment={"python": "3.12.7", "pytest": "8.3.3"},
        coverage_error=None,
    )
    return report.model_dump(mode="json")


def _coverage(files: int, rng: random.Random) -> dict[str, Any]:
    from testmind.coverage_engine.models import (  # noqa: PLC0415
        CoverageReport,
        CoverageTotals,
        FileCoverage,
        FunctionCoverage,
    )

    def counts(statements: int) -> dict[str, Any]:
        covered = rng.randint(statements // 2, statements)
        percent = round(100 * covered / statements, 2)
        return {
            "statements": statements,
            "covered_lines": covered,
            "branches": 0,
            "covered_branches": 0,
            "line_percent": percent,
            "branch_percent": 100.0,
            "percent": percent,
        }

    file_rows, functions = [], []
    for f in range(files):
        path = f"src/package/module_{f}.py"
        file_rows.append(
            FileCoverage(
                path=path,
                missing_lines=[3, 4, 9],
                missing=["3-4", "9"],
                missing_branches_detail=[],
                **counts(120),
            )
        )
        for g in range(8):
            functions.append(
                FunctionCoverage(
                    path=path,
                    qualname=f"function_{g}",
                    line=10 + g * 12,
                    end_line=20 + g * 12,
                    missing=["12"],
                    missing_branches_detail=[],
                    complexity=rng.randint(1, 9),
                    findings=[],
                    is_public=True,
                    **counts(10),
                )
            )
    report = CoverageReport(
        tool="coverage.py 7.6.1",
        branch_coverage=False,
        threshold=80.0,
        totals=CoverageTotals(
            files=files, functions=len(functions), functions_never_executed=0, **counts(120 * files)
        ),
        files=file_rows,
        functions=functions,
        gaps=[],
        unmeasured_files=[],
    )
    return report.model_dump(mode="json")


async def seed(projects: int, runs: int, executions: int, tests: int, files: int) -> None:
    from sqlalchemy import insert  # noqa: PLC0415

    from testmind.core.config import get_settings  # noqa: PLC0415
    from testmind.db.models import AnalysisRun, Project, TestExecution  # noqa: PLC0415
    from testmind.db.session import create_engine  # noqa: PLC0415
    from testmind.domain.enums import RunStatus, SourceType  # noqa: PLC0415

    rng = random.Random(14)  # noqa: S311 - synthetic data, not security
    engine = create_engine(get_settings(), pooled=False)
    start = datetime.now(UTC) - timedelta(days=30)
    # A handful of distinct documents, reused: realistic sizes without hours of generation.
    reports = [_report(tests, rng) for _ in range(5)]
    coverages = [_coverage(files, rng) for _ in range(5)]
    async with engine.begin() as connection:
        for p in range(projects):
            project_id = uuid.uuid4()
            await connection.execute(
                insert(Project).values(
                    id=project_id,
                    name=f"load-{uuid.uuid4().hex[:8]}",
                    source_type=SourceType.LOCAL,
                    source_location="sample",
                    created_at=start,
                )
            )
            run_rows, execution_rows = [], []
            for r in range(runs):
                at = start + timedelta(minutes=p * runs + r)
                run_id = uuid.uuid4()
                run_rows.append(
                    {
                        "id": run_id,
                        "project_id": project_id,
                        "created_at": at,
                        "status": RunStatus.SUCCEEDED,
                        "started_at": at,
                        "finished_at": at + timedelta(seconds=5),
                    }
                )
                for e in range(executions):
                    report = reports[(r + e) % 5]
                    coverage = coverages[(r + e) % 5]
                    execution_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "run_id": run_id,
                            "status": RunStatus.SUCCEEDED,
                            "outcome": report["outcome"],
                            "report": report,
                            "coverage": coverage,
                            "coverage_percent": coverage["totals"]["percent"],
                            "failures": {
                                "groups": [],
                                "failed_tests": 0,
                                "analysed_tests": 0,
                                "unanalysed_tests": [],
                                "notes": [],
                            },
                            "created_at": at + timedelta(seconds=6 + e),
                            "started_at": at,
                            "finished_at": at + timedelta(seconds=30 + e),
                        }
                    )
            await connection.execute(insert(AnalysisRun), run_rows)
            await connection.execute(insert(TestExecution), execution_rows)
    await engine.dispose()
    total = projects * runs
    print(f"seeded {projects} projects, {total} runs, {total * executions} executions")  # noqa: T201


# ------------------------------------------------------------------------------------- run
Endpoint = tuple[str, str]  # (label, path)


async def discover(client: httpx.AsyncClient) -> list[Endpoint]:
    overview = (await client.get("/api/v1/overview")).raise_for_status().json()
    project = overview["project_health"][0]["id"]
    runs = (await client.get(f"/api/v1/projects/{project}/runs", params={"limit": 1})).json()
    run = runs["items"][0]["id"]
    executions = (await client.get(f"/api/v1/runs/{run}/test-executions")).json()
    execution = executions["items"][0]["id"]
    return [
        ("GET /health", "/health"),
        ("GET /api/v1/overview", "/api/v1/overview"),
        ("GET /api/v1/projects", "/api/v1/projects?limit=100"),
        ("GET /api/v1/projects/{id}/overview", f"/api/v1/projects/{project}/overview"),
        ("GET /api/v1/projects/{id}/runs", f"/api/v1/projects/{project}/runs?limit=100"),
        ("GET /api/v1/runs/{id}/test-executions", f"/api/v1/runs/{run}/test-executions"),
        ("GET /api/v1/test-executions/{id}", f"/api/v1/test-executions/{execution}"),
        ("GET /api/v1/test-executions/{id}/tests", f"/api/v1/test-executions/{execution}/tests"),
        ("GET /metrics", "/metrics"),
    ]


async def measure(
    call: Callable[[], Awaitable[httpx.Response]], requests: int, concurrency: int
) -> dict[str, float]:
    latencies: list[float] = []
    errors = 0
    remaining = requests

    async def worker() -> None:
        nonlocal remaining, errors
        while remaining > 0:
            remaining -= 1
            started = time.perf_counter()
            try:
                answer = await call()
                ok = answer.status_code == HTTPStatus.OK
            except httpx.HTTPError:
                ok = False
            latencies.append(time.perf_counter() - started)
            errors += not ok

    began = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    elapsed = time.perf_counter() - began
    cuts = statistics.quantiles(latencies, n=100, method="inclusive")
    return {
        "p50": cuts[49] * 1000,
        "p95": cuts[94] * 1000,
        "p99": cuts[98] * 1000,
        "max": max(latencies) * 1000,
        "rps": len(latencies) / elapsed,
        "errors": errors,
    }


async def run(url: str, token: str | None, requests: int, concurrency: int) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    limits = httpx.Limits(max_connections=concurrency)
    async with httpx.AsyncClient(base_url=url, headers=headers, timeout=60, limits=limits) as c:
        endpoints = await discover(c)
        for _, path in endpoints:  # warm up: connections, caches, lazy imports
            await c.get(path)
        print(f"{requests} requests per endpoint, {concurrency} concurrent\n")  # noqa: T201
        print("| Endpoint | p50 ms | p95 ms | p99 ms | max ms | req/s | errors |")  # noqa: T201
        print("|---|---:|---:|---:|---:|---:|---:|")  # noqa: T201
        for label, path in endpoints:
            r = await measure(lambda path=path: c.get(path), requests, concurrency)  # type: ignore[misc]
            print(  # noqa: T201
                f"| `{label}` | {r['p50']:.1f} | {r['p95']:.1f} | {r['p99']:.1f} | "
                f"{r['max']:.1f} | {r['rps']:.0f} | {r['errors']:.0f} |"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    seeding = commands.add_parser("seed", help="insert finished jobs into the database")
    seeding.add_argument("--projects", type=int, default=50)
    seeding.add_argument("--runs", type=int, default=20, help="analysis runs per project")
    seeding.add_argument("--executions", type=int, default=1, help="test executions per run")
    seeding.add_argument("--tests", type=int, default=300, help="tests per execution report")
    seeding.add_argument("--files", type=int, default=40, help="files per coverage report")
    running = commands.add_parser("run", help="measure the read API")
    running.add_argument("--url", default="http://127.0.0.1:8000")
    running.add_argument("--requests", type=int, default=300)
    running.add_argument("--concurrency", type=int, default=10)
    args = parser.parse_args(argv)
    if args.command == "seed":
        asyncio.run(seed(args.projects, args.runs, args.executions, args.tests, args.files))
    else:
        token = os.environ.get("TESTMIND_API_TOKEN") or None
        asyncio.run(run(args.url, token, args.requests, args.concurrency))
    return 0


if __name__ == "__main__":
    sys.exit(main())
