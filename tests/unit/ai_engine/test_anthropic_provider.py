from dataclasses import dataclass, field
from typing import Any

import anthropic
import httpx2
import pytest

from testmind.ai_engine.llm import LLMError, LLMRefusalError, LLMRequest
from testmind.ai_engine.llm.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from testmind.ai_engine.llm.factory import create_provider, llm_available
from testmind.ai_engine.llm.redaction import RedactingProvider
from testmind.core.config import Settings


@dataclass
class Block:
    type: str
    text: str = ""


@dataclass
class Usage:
    input_tokens: int = 11
    output_tokens: int = 22


@dataclass
class StopDetails:
    category: str | None


@dataclass
class Response:
    content: list[Block]
    stop_reason: str = "end_turn"
    model: str = "claude-opus-5"
    usage: Usage = field(default_factory=Usage)
    stop_details: StopDetails | None = None


class FakeMessages:
    def __init__(self, outcome: Response | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeClient:
    """Mirrors the `client.beta.messages.create` shape of anthropic.Anthropic."""

    def __init__(self, outcome: Response | Exception) -> None:
        self.messages = FakeMessages(outcome)
        self.beta = self


REQUEST = LLMRequest(system="sys", prompt="write tests", max_tokens=1234)


def test_request_shape_and_response_mapping() -> None:
    client = FakeClient(
        Response(content=[Block("thinking"), Block("text", "part 1 "), Block("text", "part 2")])
    )
    response = AnthropicProvider(client, "claude-opus-5").complete(REQUEST)

    assert response.text == "part 1 part 2"
    assert (response.model, response.input_tokens, response.output_tokens) == (
        "claude-opus-5",
        11,
        22,
    )
    assert client.messages.calls == [
        {
            "model": "claude-opus-5",
            "max_tokens": 1234,
            "system": "sys",
            "messages": [{"role": "user", "content": "write tests"}],
            "betas": [FALLBACK_BETA],
            "fallbacks": "default",
        }
    ]


def test_model_reported_is_the_one_that_answered() -> None:
    client = FakeClient(Response(content=[Block("text", "x")], model="claude-opus-4-8"))
    assert AnthropicProvider(client, "claude-opus-5").complete(REQUEST).model == "claude-opus-4-8"


def test_refusal() -> None:
    client = FakeClient(
        Response(content=[], stop_reason="refusal", stop_details=StopDetails("cyber"))
    )
    with pytest.raises(LLMRefusalError, match="category: cyber"):
        AnthropicProvider(client, "m").complete(REQUEST)


def test_truncated_response() -> None:
    client = FakeClient(Response(content=[Block("text", "partial")], stop_reason="max_tokens"))
    with pytest.raises(LLMError, match="truncated"):
        AnthropicProvider(client, "m").complete(REQUEST)


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def test_api_status_error_is_wrapped() -> None:
    error = anthropic.RateLimitError(
        "rate limited", response=httpx2.Response(429, request=_request()), body=None
    )
    with pytest.raises(LLMError, match="429"):
        AnthropicProvider(FakeClient(error), "m").complete(REQUEST)


def test_connection_error_is_wrapped() -> None:
    error = anthropic.APIConnectionError(request=_request())
    with pytest.raises(LLMError, match="Could not reach"):
        AnthropicProvider(FakeClient(error), "m").complete(REQUEST)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@db/x",
        "redis_url": "redis://r:6379/0",
        "repos_root": "/srv",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.fixture
def no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("no_credentials")
def test_not_available_without_credentials() -> None:
    assert llm_available(settings()) is False
    with pytest.raises(LLMError, match="No LLM provider is configured"):
        create_provider(settings())


@pytest.mark.usefixtures("no_credentials")
def test_disabled_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
    assert llm_available(settings(llm_provider="disabled")) is False


@pytest.mark.usefixtures("no_credentials")
def test_available_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder-value")
    assert llm_available(settings()) is True
    provider = create_provider(settings(llm_model="claude-opus-5"))
    # Every provider is wrapped: prompts are redacted before they leave.
    assert isinstance(provider, RedactingProvider)
    assert isinstance(provider._inner, AnthropicProvider)
    assert provider.name == "anthropic"
