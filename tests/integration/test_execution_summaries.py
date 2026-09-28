"""Execution summaries read the report without its per-test list and logs, and none of the
other documents; the answer must be exactly the one built from the whole row."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.db.models import AnalysisRun, Project, TestExecution
from testmind.domain.enums import RunStatus, SourceType
from testmind.repositories.execution_repository import ExecutionRepository
from testmind.sandbox.models import Outcome
from testmind.schemas.test_execution import TestExecutionRead
from testmind.test_engine.models import (
    ExecutionOutcome,
    InstallReport,
    TestCaseReport,
    TestRunReport,
    TestTotals,
)

T0 = datetime(2026, 9, 1, tzinfo=UTC)


def report(*, install: bool, coverage_error: str | None = None) -> dict[str, Any]:
    tests = [
        TestCaseReport(
            nodeid=f"tests/test_a.py::test_{i}",
            file="tests/test_a.py",
            line=i,
            name=f"test_{i}",
            outcome=Outcome.FAILED if i == 0 else Outcome.PASSED,
            duration_seconds=0.1,
            message="boom" if i == 0 else None,
            traceback="Traceback" if i == 0 else None,
            generated=i == 2,
        )
        for i in range(3)
    ]
    return TestRunReport(
        outcome=ExecutionOutcome.FAILED, exit_code=1, exit_meaning="some tests failed",
        totals=TestTotals(total=3, passed=2, failed=1), duration_seconds=1.5, tests=tests,
        output="." * 1000,
        install=InstallReport(image="testmind-runner:abc", requirements=["requests"],
                              from_cache=False, notes=["pinned requests"], log="x" * 1000)
        if install else None,
        project_test_files=["tests/test_a.py"], generated_test_files=["tests/test_gen.py"],
        environment={"python": "3.12"}, coverage_error=coverage_error,
    ).model_dump(mode="json")  # fmt: skip


@pytest.fixture
async def executions(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, uuid.UUID]:
    async with session_factory() as session:
        project = Project(name="p", source_type=SourceType.LOCAL, source_location="sample")
        session.add(project)
        await session.flush()
        run = AnalysisRun(project_id=project.id, status=RunStatus.SUCCEEDED)
        session.add(run)
        await session.flush()
        rows = {
            "installed": TestExecution(
                run_id=run.id, status=RunStatus.SUCCEEDED, outcome=ExecutionOutcome.FAILED,
                report=report(install=True), coverage={"large": "x" * 5000},
                coverage_percent=81.5, api_coverage={"large": "y"}, api_coverage_percent=40.0,
                failures={"groups": [{"id": "g1"}, {"id": "g2"}]}, created_at=T0,
            ),
            "no_install": TestExecution(
                run_id=run.id, status=RunStatus.SUCCEEDED, outcome=ExecutionOutcome.FAILED,
                report=report(install=False, coverage_error="coverage.py crashed"),
                created_at=T0 + timedelta(minutes=1),
            ),
            "queued": TestExecution(
                run_id=run.id, status=RunStatus.QUEUED, created_at=T0 + timedelta(minutes=2)
            ),
        }  # fmt: skip
        session.add_all(rows.values())
        await session.commit()
        return {name: row.id for name, row in rows.items()} | {"run": run.id}


async def test_a_summary_is_the_answer_built_from_the_whole_row(
    session_factory: async_sessionmaker[AsyncSession], executions: dict[str, uuid.UUID]
) -> None:
    for name in ("installed", "no_install", "queued"):
        async with session_factory() as session:
            whole = TestExecutionRead.from_row(
                await session.get(TestExecution, executions[name])  # type: ignore[arg-type]
            )
        async with session_factory() as session:
            row = await ExecutionRepository(session).summary(executions[name])
            assert row is not None
            assert TestExecutionRead.from_summary(row) == whole, name
            assert (whole.summary is None) == (name == "queued")


async def test_summaries_leave_the_documents_in_the_database(
    session_factory: async_sessionmaker[AsyncSession], executions: dict[str, uuid.UUID]
) -> None:
    async with session_factory() as session:
        row = await ExecutionRepository(session).summary(executions["installed"])
        assert row is not None
        assert row.report is not None
        assert "tests" not in row.report
        assert "output" not in row.report
        assert "log" not in row.report["install"]
        assert row.report["install"]["notes"] == ["pinned requests"]
        assert row.failure_groups == 2
        for document in ("report", "coverage", "failures", "api_coverage"):
            with pytest.raises(InvalidRequestError):
                getattr(row.execution, document)


async def test_summaries_for_a_run_newest_first(
    session_factory: async_sessionmaker[AsyncSession], executions: dict[str, uuid.UUID]
) -> None:
    async with session_factory() as session:
        repository = ExecutionRepository(session)
        rows, total = await repository.summaries_for_run(executions["run"], offset=0, limit=2)
        assert total == 3
        assert [r.execution.id for r in rows] == [executions["queued"], executions["no_install"]]
        assert await repository.summary(uuid.uuid4()) is None
