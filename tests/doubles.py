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
        self.executions: list[uuid.UUID] = []
        self.failure_analyses: list[uuid.UUID] = []
        self.regressions: list[uuid.UUID] = []
        self.mutation_runs: list[uuid.UUID] = []
        self.mutant_suggestions: list[uuid.UUID] = []

    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        self.dispatched.append(run_id)

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        self.generations.append(generation_id)

    async def dispatch_test_execution(self, execution_id: uuid.UUID) -> None:
        self.executions.append(execution_id)

    async def dispatch_failure_analysis(self, analysis_id: uuid.UUID) -> None:
        self.failure_analyses.append(analysis_id)

    async def dispatch_regression(self, regression_id: uuid.UUID) -> None:
        self.regressions.append(regression_id)

    async def dispatch_mutation_run(self, mutation_run_id: uuid.UUID) -> None:
        self.mutation_runs.append(mutation_run_id)

    async def dispatch_mutant_suggestions(self, suggestion_id: uuid.UUID) -> None:
        self.mutant_suggestions.append(suggestion_id)


class FailingDispatcher:
    async def dispatch_analysis_run(self, run_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_test_generation(self, generation_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_test_execution(self, execution_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_failure_analysis(self, analysis_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_regression(self, regression_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_mutation_run(self, mutation_run_id: uuid.UUID) -> None:
        raise ConnectionError("broker unavailable")

    async def dispatch_mutant_suggestions(self, suggestion_id: uuid.UUID) -> None:
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
