"""No doubles: service → Celery (Redis) → real worker → git objects → Docker sandbox → DB."""

import asyncio
from pathlib import Path

import pytest
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.db.models import Regression
from testmind.domain.enums import RunStatus
from testmind.regression.models import RegressionMode, RegressionReport, TestChangeKind, Verdict
from testmind.schemas.project import ProjectCreate
from testmind.services.project_service import ProjectService
from testmind.services.regression_service import RegressionService
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.regression_fixture import make_shop_repo

pytestmark = pytest.mark.integration


@pytest.mark.usefixtures("worker")
async def test_regression_through_the_whole_stack(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    make_shop_repo(repos_root / "shop")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="shop", source_location="shop")
        )
        regression = await RegressionService(
            session, CeleryTaskDispatcher(celery_app), repos_root
        ).create(project.id, "HEAD~1", "HEAD", RegressionMode.FULL)

    async with asyncio.timeout(180):
        while True:
            async with session_factory() as session:
                row = await session.get(Regression, regression.id)
                if row is not None and row.status.is_terminal:
                    break
            await asyncio.sleep(0.5)

    assert row.status is RunStatus.SUCCEEDED, row.error_message
    assert row.verdict is Verdict.REGRESSIONS
    report = RegressionReport.model_validate(row.report)
    regressed = sorted(t.nodeid for t in report.tests if t.change is TestChangeKind.REGRESSED)
    assert regressed == ["tests/test_cart.py::test_total", "tests/test_pricing.py::test_discount"]
    assert report.selection_check is not None
    assert report.selection_check.missed == []
