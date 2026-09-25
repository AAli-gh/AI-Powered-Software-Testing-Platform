"""End-to-end through the real queue: API service → Celery (Redis) → worker → PostgreSQL."""

import asyncio
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from celery import Celery
from celery.contrib.testing.worker import start_worker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.config import Settings
from testmind.db.models import AnalysisRun
from testmind.domain.enums import RunStatus
from testmind.schemas.project import ProjectCreate
from testmind.services.project_service import ProjectService
from testmind.services.run_service import RunService
from testmind.workers.celery_app import create_celery_app
from testmind.workers.dispatcher import CeleryTaskDispatcher

WAIT_SECONDS = 20


@pytest.fixture
def celery_app(settings: Settings) -> Celery:
    app = create_celery_app(settings)
    app.control.purge()  # drop messages left over from other tests
    return app


@pytest.fixture
def worker(celery_app: Celery) -> Iterator[None]:
    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        yield


async def wait_for_terminal(
    session_factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID
) -> AnalysisRun:
    async with asyncio.timeout(WAIT_SECONDS):
        while True:
            async with session_factory() as session:
                run = await session.get(AnalysisRun, run_id)
                if run is not None and run.status.is_terminal:
                    return run
            await asyncio.sleep(0.1)


async def queue_run(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> uuid.UUID:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
        run = await RunService(session, CeleryTaskDispatcher(celery_app)).create(project.id)
        return run.id


@pytest.mark.usefixtures("worker")
async def test_run_succeeds_through_the_real_queue(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    run_id = await queue_run(session_factory, celery_app, repos_root)

    run = await wait_for_terminal(session_factory, run_id)

    assert run.status is RunStatus.SUCCEEDED
    assert run.started_at is not None
    assert run.finished_at is not None


@pytest.mark.usefixtures("worker")
async def test_run_fails_when_source_disappears(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    async with session_factory() as session:
        project = await ProjectService(session, repos_root).create(
            ProjectCreate(name="demo", source_location="sample")
        )
    shutil.rmtree(repos_root / "sample")
    async with session_factory() as session:
        run = await RunService(session, CeleryTaskDispatcher(celery_app)).create(project.id)

    finished = await wait_for_terminal(session_factory, run.id)

    assert finished.status is RunStatus.FAILED
    assert finished.error_message is not None


async def test_run_cancelled_before_pickup_is_skipped_by_worker(
    session_factory: async_sessionmaker[AsyncSession], celery_app: Celery, repos_root: Path
) -> None:
    run_id = await queue_run(session_factory, celery_app, repos_root)  # no worker running yet
    async with session_factory() as session:
        await RunService(session, CeleryTaskDispatcher(celery_app)).cancel(run_id)

    with start_worker(celery_app, pool="solo", perform_ping_check=False, loglevel="WARNING"):
        await asyncio.sleep(2)  # give the worker time to consume the queued message

    async with session_factory() as session:
        run = await session.get(AnalysisRun, run_id)
        assert run is not None
        assert run.status is RunStatus.CANCELLED
        assert run.started_at is None
