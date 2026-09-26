"""Claude via the official Anthropic SDK."""

from __future__ import annotations

from typing import Any

import anthropic

from testmind.ai_engine.llm.base import LLMError, LLMRefusalError, LLMRequest, LLMResponse
from testmind.core.logging import get_logger

logger = get_logger(__name__)

# Server-side refusal fallback: if the primary model declines, the API re-runs the same request
# on a fallback model chosen by refusal category, inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: Any, model: str) -> None:
        # `client` is an anthropic.Anthropic (typed as Any so tests can pass a stand-in).
        self._client = client
        self._model = model

    @classmethod
    def from_environment(cls, model: str, timeout_seconds: float) -> AnthropicProvider:
        """Credentials are resolved by the SDK itself (ANTHROPIC_API_KEY, auth token, profile)."""
        return cls(anthropic.Anthropic(timeout=timeout_seconds), model)

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            response = self._client.beta.messages.create(
                model=self._model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.prompt}],
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIStatusError as exc:
            logger.warning("llm_request_failed", status=exc.status_code, request_id=exc.request_id)
            raise LLMError(f"Anthropic API error {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Anthropic API") from exc

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise LLMRefusalError(f"The model declined the request (category: {category})")
        if response.stop_reason == "max_tokens":
            raise LLMError("The response was truncated (max_tokens reached)")

        text = "".join(block.text for block in response.content if block.type == "text")
        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
