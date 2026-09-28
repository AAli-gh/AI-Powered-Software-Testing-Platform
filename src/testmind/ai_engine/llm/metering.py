"""Every LLM completion is counted: outcome, duration and tokens (docs/monitoring.md).

Tokens are what an LLM provider bills, so `testmind_llm_tokens_total` is the number to watch
for cost. `MeteredProvider` wraps the configured provider in the factory, like redaction.
"""

from __future__ import annotations

import time

from testmind.ai_engine.llm.base import LLMProvider, LLMRefusalError, LLMRequest, LLMResponse
from testmind.core.metrics import LLM_DURATION, LLM_REQUESTS, LLM_TOKENS


class MeteredProvider:
    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name

    def complete(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        outcome = "error"
        try:
            response = self._inner.complete(request)
            outcome = "ok"
        except LLMRefusalError:
            outcome = "refused"
            raise
        finally:
            LLM_REQUESTS.labels(outcome).inc()
            LLM_DURATION.labels(outcome).observe(time.monotonic() - started)
        LLM_TOKENS.labels("input").inc(response.input_tokens)
        LLM_TOKENS.labels("output").inc(response.output_tokens)
        return response
