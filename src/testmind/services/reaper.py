"""Recover jobs orphaned by a worker that died mid-job.

A job is picked up only while `queued` (so re-delivered messages never run it twice). If the
worker is killed after moving a job to `running` (OOM, host restart), nothing would ever finish
it. At worker start-up, jobs that have been `running` for longer than the task time limit —
after which Celery kills any task anyway — are marked failed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from testmind.core.logging import get_logger
from testmind.db.models import (
    AnalysisRun,
    FailureAnalysis,
    Regression,
    TestExecution,
    TestGeneration,
)
from testmind.domain.enums import RunStatus
from testmind.services.run_service import apply_transition

logger = get_logger(__name__)

ORPHANED_MESSAGE = "The worker stopped while this job was running; the job was abandoned"


JobT = TypeVar("JobT", AnalysisRun, TestGeneration, TestExecution, FailureAnalysis, Regression)


async def reap_orphaned_jobs(
    session_factory: async_sessionmaker[AsyncSession], *, older_than: timedelta
) -> int:
    cutoff = datetime.now(UTC) - older_than
    async with session_factory() as session:
        reaped = (
            await _reap(session, AnalysisRun, cutoff)
            + await _reap(session, TestGeneration, cutoff)
            + await _reap(session, TestExecution, cutoff)
            + await _reap(session, FailureAnalysis, cutoff)
            + await _reap(session, Regression, cutoff)
        )
        await session.commit()
    return reaped


async def _reap(session: AsyncSession, model: type[JobT], cutoff: datetime) -> int:
    result = await session.execute(
        select(model)
        .where(model.status == RunStatus.RUNNING, model.started_at < cutoff)
        .with_for_update(skip_locked=True)
    )
    jobs = list(result.scalars())
    for job in jobs:
        apply_transition(job, RunStatus.FAILED, error_message=ORPHANED_MESSAGE)
        logger.warning("orphaned_job_reaped", table=model.__tablename__, job_id=str(job.id))
    return len(jobs)
