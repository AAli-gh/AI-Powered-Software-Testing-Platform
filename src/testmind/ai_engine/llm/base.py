"""The contract every LLM provider implements; only `ai_engine.llm` imports a vendor SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class LLMRequest:
    system: str
    prompt: str
    max_tokens: int


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    model: str  # the model that actually produced the answer (may differ after a fallback)
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Blocking completion. Raises LLMError (or a subclass) on failure."""
        ...


class LLMError(Exception):
    """The provider could not produce a usable answer."""


class LLMRefusalError(LLMError):
    """The model declined the request (safety classifier)."""


class LLMNotConfiguredError(LLMError):
    """No provider is enabled or no credentials are available."""
