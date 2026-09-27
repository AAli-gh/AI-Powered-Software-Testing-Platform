"""No doubles: service → Celery (Redis) → real worker → Docker sandbox (driver) → DB."""

import asyncio
import shutil
from pathlib import Path

import pytest
from celery import Celery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import Settings
from testmind.db.models import MutationRun
from testmind.domain.enums import RunStatus
from testmind.mutation_engine.models import MutationReport
from testmind.schemas.project import ProjectCreate
from testmind.services.mutation_service import MutationOptions, MutationService
from testmind.services.project_service import ProjectService
from testmind.services.run_executor import RunExecutor
from testmind.services.run_service import RunService
from testmind.workers.dispatcher import CeleryTaskDispatcher
from tests.doubles import RecordingDispatcher
from tests.sandbox.test_suite_runner import EXAMPLES

pytestmark = pytest.mark.integration


@pytest.mark.usefixtures("worker")
async def test_mutation_testing_through_the_whole_stack(
    session_factory: async_sessionmaker[AsyncSession],
    celery_app: Celery,
    repos_root: Path,
    settings: Settings,
) -> None:
    shutil.copytree(EXAMPLES / "shipping_rates", repos_root / "shipping")
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="shipping", source_location="shipping")
        )
        run = await RunService(session, RecordingDispatcher()).create(project.id)
    await RunExecutor(session_factory, repos_root).execute(run.id)
    async with session_factory() as session:
        job = await MutationService(session, CeleryTaskDispatcher(celery_app), settings).create(
            run.id, MutationOptions()
        )

    async with asyncio.timeout(300):
        while True:
            async with session_factory() as session:
                row = await session.get(MutationRun, job.id)
                if row is not None and row.status.is_terminal:
                    break
            await asyncio.sleep(0.5)

    assert row.status is RunStatus.SUCCEEDED, row.error_message
    report = MutationReport.model_validate(row.report)
    assert (report.totals.total, report.totals.survived, row.score) == (20, 8, 60.0)
