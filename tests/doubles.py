"""Test doubles for the *queue boundary only*.

The database is never faked. The real Celery dispatcher is covered end-to-end in
tests/integration/test_worker.py; these doubles let API/service tests assert what was enqueued
without a running worker.
"""

from __future__ import annotations

import uuid


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[uuid.UUID] = []

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        self.dispatched.append(run_id)


class FailingDispatcher:
    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")
