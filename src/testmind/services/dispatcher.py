from __future__ import annotations

import uuid
from typing import Protocol


class TaskDispatcher(Protocol):
    """Port through which services hand long-running work to the background queue.

    The production implementation is `testmind.workers.dispatcher.CeleryTaskDispatcher`.
    """

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None: ...

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None: ...
