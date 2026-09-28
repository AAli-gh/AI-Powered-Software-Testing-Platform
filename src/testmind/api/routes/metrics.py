"""Prometheus metrics of the API process, and of the platform's state (docs/monitoring.md).

The HTTP metrics are recorded as requests are answered. The state (jobs by kind and status,
the age of the oldest waiting and running job, the queue's length) is read from PostgreSQL
and Redis when Prometheus scrapes, so it is the same whichever API process answers. A
dependency that cannot be read is reported as down (`testmind_dependency_up 0`) and its
series are left out: the scrape itself still succeeds.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Gauge,
    Info,
    generate_latest,
)

from testmind import __version__
from testmind.core.logging import get_logger
from testmind.domain.enums import RunStatus
from testmind.repositories.overview_repository import (
    JOB_MODELS,
    JobStatistics,
    OverviewRepository,
)
from testmind.workers.celery_app import DEFAULT_QUEUE

router = APIRouter(tags=["monitoring"])
logger = get_logger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0
# Kombu's Redis transport keeps a queue in one list per priority step: the queue's name for
# the default priority, `<name>\x06\x16<step>` for the others.
QUEUE_KEYS = (DEFAULT_QUEUE, *(f"{DEFAULT_QUEUE}\x06\x16{step}" for step in (3, 6, 9)))


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    state = CollectorRegistry()
    Info("testmind_build", "The running TestMind version", registry=state).info(
        {"version": __version__}
    )
    up = Gauge(
        "testmind_dependency_up",
        "Whether the dependency could be read for this scrape",
        ["dependency"],
        registry=state,
    )
    statistics, queued = await asyncio.gather(_job_statistics(request), _queue_length(request))
    up.labels("database").set(statistics is not None)
    up.labels("redis").set(queued is not None)
    if statistics is not None:
        _job_gauges(statistics, state)
    if queued is not None:
        Gauge(
            "testmind_queue_messages",
            "Messages waiting in the worker queue (jobs not yet picked up)",
            registry=state,
        ).set(queued)
    body = generate_latest(REGISTRY) + generate_latest(state)
    return Response(body, media_type=CONTENT_TYPE_LATEST)


def _job_gauges(statistics: list[JobStatistics], registry: CollectorRegistry) -> None:
    jobs = Gauge(
        "testmind_jobs",
        "Jobs by kind and status",
        ["kind", "status"],
        registry=registry,
    )
    oldest = Gauge(
        "testmind_oldest_job_age_seconds",
        "Age of the oldest queued job (since created) and running job (since started); "
        "0 when there is none",
        ["status"],
        registry=registry,
    )
    for model in JOB_MODELS:  # every series present, zeros included
        for status in RunStatus:
            jobs.labels(model.__tablename__, status.value).set(0)
    now = time.time()
    ages = {RunStatus.QUEUED: 0.0, RunStatus.RUNNING: 0.0}
    for row in statistics:
        jobs.labels(row.kind, row.status.value).set(row.count)
        since = row.oldest_created if row.status is RunStatus.QUEUED else row.oldest_started
        if row.status in ages and since is not None:
            ages[row.status] = max(ages[row.status], now - since.timestamp())
    for status, age in ages.items():
        oldest.labels(status.value).set(max(age, 0.0))


async def _job_statistics(request: Request) -> list[JobStatistics] | None:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with request.app.state.session_factory() as session:
                return await OverviewRepository(session).job_statistics()
    except Exception as exc:
        logger.warning("metrics_unavailable", dependency="database", error=repr(exc))
        return None


async def _queue_length(request: Request) -> int | None:
    redis: Any = request.app.state.redis
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with redis.pipeline(transaction=False) as pipe:
                for key in QUEUE_KEYS:
                    pipe.llen(key)
                lengths = await pipe.execute()
    except Exception as exc:
        logger.warning("metrics_unavailable", dependency="redis", error=repr(exc))
        return None
    return int(sum(lengths))
