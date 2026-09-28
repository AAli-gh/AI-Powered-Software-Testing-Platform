"""LLM calls are counted by outcome, with their duration and tokens."""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from testmind.ai_engine.llm.base import (
    LLMError,
    LLMRefusalError,
    LLMRequest,
    LLMResponse,
)
from testmind.ai_engine.llm.metering import MeteredProvider

REQUEST = LLMRequest(system="s", prompt="p", max_tokens=10)


class Scripted:
    name = "scripted"

    def __init__(self, outcome: LLMResponse | Exception) -> None:
        self.outcome = outcome

    def complete(self, request: LLMRequest) -> LLMResponse:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def sample(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def snapshot() -> tuple[float, ...]:
    return (
        sample("testmind_llm_requests_total", outcome="ok"),
        sample("testmind_llm_requests_total", outcome="refused"),
        sample("testmind_llm_requests_total", outcome="error"),
        sample("testmind_llm_tokens_total", direction="input"),
        sample("testmind_llm_tokens_total", direction="output"),
        sum(
            sample("testmind_llm_request_duration_seconds_count", outcome=outcome)
            for outcome in ("ok", "refused", "error")
        ),
    )


def delta(before: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(now - then for now, then in zip(snapshot(), before, strict=True))


def test_a_completion_counts_its_tokens() -> None:
    before = snapshot()
    answer = LLMResponse(text="ok", model="m", input_tokens=120, output_tokens=30)
    provider = MeteredProvider(Scripted(answer))
    assert provider.name == "scripted"
    assert provider.complete(REQUEST) is answer
    assert delta(before) == (1, 0, 0, 120, 30, 1)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (LLMRefusalError("declined"), (0, 1, 0, 0, 0, 1)),
        (LLMError("overloaded"), (0, 0, 1, 0, 0, 1)),
        (RuntimeError("bug"), (0, 0, 1, 0, 0, 1)),
    ],
)
def test_failures_are_counted_and_raised(failure: Exception, expected: tuple[float, ...]) -> None:
    before = snapshot()
    with pytest.raises(type(failure)):
        MeteredProvider(Scripted(failure)).complete(REQUEST)
    assert delta(before) == expected
