"""Test doubles for external boundaries only (task queue, LLM provider).

The database is never faked. The real Celery dispatcher is covered end-to-end in
tests/integration/test_worker.py; these doubles let API/service tests assert what was enqueued
without a running worker.
"""

from __future__ import annotations

import uuid

from testmind.ai_engine.llm import LLMRequest, LLMResponse


class RecordingDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[uuid.UUID] = []
        self.generations: list[uuid.UUID] = []

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        self.dispatched.append(run_id)

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        self.generations.append(generation_id)


class FailingDispatcher:
    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")


class ScriptedLLM:
    """Stands in for the LLM *provider boundary* (no API key in CI).

    Returns the given replies in order and records every request, so tests can assert what
    the generator sent. Everything downstream (static checks, the real Docker sandbox, the fix
    loop, persistence) runs for real.
    """

    name = "scripted"

    def __init__(self, replies: list[str | Exception], model: str = "scripted-model") -> None:
        self._replies = list(replies)
        self.model = model
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, model=self.model, input_tokens=100, output_tokens=50)
