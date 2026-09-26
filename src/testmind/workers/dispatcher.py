from __future__ import annotations

import asyncio
import uuid

from celery import Celery

from testmind.workers.tasks import EXECUTE_ANALYSIS_RUN, EXECUTE_TESTS, GENERATE_TESTS


class CeleryTaskDispatcher:
    """`TaskDispatcher` backed by Celery. Publishing is blocking I/O, so it runs in a thread."""

    def __init__(self, app: Celery) -> None:
        self._app = app

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, EXECUTE_ANALYSIS_RUN, args=[str(run_id)])

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, GENERATE_TESTS, args=[str(generation_id)])

    async def dispatch_test_execution(self, execution_id: uuid.UUID) -> None:
        await asyncio.to_thread(self._app.send_task, EXECUTE_TESTS, args=[str(execution_id)])
