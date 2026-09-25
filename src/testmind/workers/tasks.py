from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from testmind.core.config import Settings
from testmind.db.session import create_engine, create_session_factory
from testmind.domain.enums import RunStatus
from testmind.services.run_executor import RunExecutor

EXECUTE_ANALYSIS_RUN = "testmind.analysis_run.execute"


def register_tasks(app: Celery, settings: Settings) -> None:
    # shared=False: Celery would otherwise attach this closure (and its `settings`) to every
    # Celery app created later in the process, where it would shadow that app's own task.
    @app.task(name=EXECUTE_ANALYSIS_RUN, shared=False)
    def execute_analysis_run(run_id: str) -> str | None:
        status = asyncio.run(_execute_analysis_run(settings, uuid.UUID(run_id)))
        return status.value if status else None


async def _execute_analysis_run(settings: Settings, run_id: uuid.UUID) -> RunStatus | None:
    # A fresh, unpooled engine per task: asyncio.run() creates a new event loop each time and
    # asyncpg connections are bound to the loop that opened them.
    engine = create_engine(settings, pooled=False)
    try:
        executor = RunExecutor(create_session_factory(engine), settings.repos_root)
        return await executor.execute(run_id)
    finally:
        await engine.dispose()
