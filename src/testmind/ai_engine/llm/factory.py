from __future__ import annotations

import os

from testmind.ai_engine.llm.anthropic_provider import AnthropicProvider
from testmind.ai_engine.llm.base import LLMNotConfiguredError, LLMProvider
from testmind.ai_engine.llm.metering import MeteredProvider
from testmind.ai_engine.llm.redaction import RedactingProvider
from testmind.core.config import Settings

ANTHROPIC_CREDENTIAL_VARIABLES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE")


def llm_available(settings: Settings) -> bool:
    """Cheap check used by the API before queueing work (no network call)."""
    if settings.llm_provider == "disabled":
        return False
    return any(os.environ.get(name) for name in ANTHROPIC_CREDENTIAL_VARIABLES)


def create_provider(settings: Settings) -> LLMProvider:
    if not llm_available(settings):
        raise LLMNotConfiguredError(
            "No LLM provider is configured: set TESTMIND_LLM_PROVIDER=anthropic and "
            "ANTHROPIC_API_KEY (see .env.example)"
        )
    # Every prompt is redacted before it leaves (redaction.py), and every call is counted
    # (metering.py).
    return MeteredProvider(
        RedactingProvider(
            AnthropicProvider.from_environment(settings.llm_model, settings.llm_timeout_seconds)
        )
    )
