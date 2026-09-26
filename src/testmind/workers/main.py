"""Celery entry point: `celery -A testmind.workers.main worker`."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from celery.signals import setup_logging, worker_ready

from testmind.core.config import get_settings
from testmind.core.logging import configure_logging
from testmind.db.session import create_engine, create_session_factory
from testmind.services.reaper import reap_orphaned_jobs
from testmind.workers.celery_app import create_celery_app

settings = get_settings()
app = create_celery_app(settings)


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    # Connecting to this signal stops Celery from installing its own log handlers.
    configure_logging(settings.log_level, json=settings.log_json)


@worker_ready.connect
def _reap_orphaned_jobs(**_: Any) -> None:
    asyncio.run(reap_on_startup())


async def reap_on_startup() -> int:
    """Jobs left `running` by a crashed worker for longer than any task may run are failed."""
    engine = create_engine(settings, pooled=False)
    try:
        return await reap_orphaned_jobs(
            create_session_factory(engine),
            older_than=timedelta(seconds=settings.celery_task_time_limit_seconds),
        )
    finally:
        await engine.dispose()
