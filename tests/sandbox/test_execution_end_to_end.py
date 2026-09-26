"""No doubles: service → Celery (Redis) → real worker → Docker sandbox → PostgreSQL."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import Settings
from testmind.coverage_engine import CoverageReport
from testmind.db.models import TestExecution
from testmind.domain.enums import RunStatus
from testmind.failure_engine import FailureCategory, FailureReport
from testmind.services.execution_service import ExecutionService
from testmind.test_engine.models import ExecutionOutcome, TestRunReport
from testmind.workers.celery_app import create_celery_app
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.integration.test_worker import queue_run, wait_for_terminal
from tests.sandbox.conftest import SANDBOX_IMAGE

pytestmark = pytest.mark.integration


@pytest.fixture
def celery_app(settings: Settings) -> Celery:
    app = create_celery_app(settings.model_copy(update={"sandbox_image": SANDBOX_IMAGE}))
    app.control.purge()
    return app


@pytest.fixture
def worker(celery_app: Celery) -> Iterator[None]:
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        yield


@pytest.mark.usefixtures("worker")
async def test_repository_suite_runs_through_the_whole_stack(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    sample = repos_root / "sample"
    (sample / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (sample / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n\n"
        "def test_wrong():\n    assert add(1, 1) == 3\n"
    )
    run_id = await queue_run(session_factory, celery_app, repos_root)
    await wait_for_terminal(session_factory, run_id)

    async with session_factory() as session:
        execution = await ExecutionService(session, CeleryTaskDispatcher(celery_app)).create(
            run_id, None
        )

    async with asyncio.timeout(120):
        while True:
            async with session_factory() as session:
                row = await session.get(TestExecution, execution.id)
                if row is not None and row.status.is_terminal:
                    break
            await asyncio.sleep(0.2)

    assert row.status is RunStatus.SUCCEEDED, row.error_message
    assert row.outcome is ExecutionOutcome.FAILED
    report = TestRunReport.model_validate(row.report)
    assert (report.totals.passed, report.totals.failed) == (1, 1)
    [failure] = report.failures()
    assert failure.nodeid == "test_calc.py::test_wrong"
    assert failure.line == 6
    # Production modules only: calc.py is fully covered, main.py (print('hello')) is never
    # imported by a test; the test module itself is not counted.
    assert row.coverage_percent == 66.67
    coverage = CoverageReport.model_validate(row.coverage)
    assert [(f.path, f.percent) for f in coverage.files] == [("calc.py", 100.0), ("main.py", 0.0)]
    assert coverage.tool == "coverage.py 7.16.1"
    # The deterministic failure analysis ran in the worker too.
    failures = FailureReport.model_validate(row.failures)
    [group] = failures.groups
    assert group.category is FailureCategory.ASSERTION_FAILURE
    assert [t.nodeid for t in group.tests] == ["test_calc.py::test_wrong"]
    assert "assert 2 == 3" in group.message
