"""Secrets are removed from every prompt before it leaves for an LLM provider.

Prompts carry repository source, test output and, for failure analysis, the `repr` of local
variables; any of them can hold a credential. `RedactingProvider` wraps the configured
provider, so no caller can forget it. What is removed:
- private keys (PEM blocks);
- tokens with a recognisable format (AWS, GitHub, Anthropic, OpenAI-style, Slack, Google,
  Stripe, JWT);
- the password of a URL (`scheme://user:password@host`);
- quoted literals assigned to a secret-looking name (`password = "…"`, `"api_key": "…"`), and
  `SECRET=…` lines in environment-file style.

Code is kept: `token = request.headers["Authorization"]` is not a secret, and the model needs
it to write tests. Only the count of redactions is logged, never a value.
"""

from __future__ import annotations

import re
from dataclasses import replace

from testmind.ai_engine.llm.base import LLMProvider, LLMRequest, LLMResponse
from testmind.core.logging import get_logger

logger = get_logger(__name__)

MARK = "[REDACTED]"
SENSITIVE_NAME = (
    r"[A-Za-z0-9_.-]*(?:password|passwd|passphrase|secret|token|api[_-]?key|apikey|"
    r"access[_-]?key|private[_-]?key|client[_-]?secret|credentials?|auth[_-]?key)[A-Za-z0-9_.-]*"
)

PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED PRIVATE KEY]",
    ),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MARK),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"), MARK),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"), MARK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), MARK),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}"), MARK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MARK),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), MARK),
    (re.compile(r"\b[rs]k_(?:live|test)_[0-9A-Za-z]{16,}\b"), MARK),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), MARK),
    # scheme://user:password@host -> scheme://user:[REDACTED]@host
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)[^\s/@]+(@)"), rf"\1{MARK}\2"),
    # password = "…", "api_key": '…', token=b"…"
    (
        re.compile(
            rf"(\b{SENSITIVE_NAME}['\"]?\s*(?::|=|==)\s*b?)(['\"])(?!\[REDACTED)([^'\"\n]{{4,}})\2",
            re.IGNORECASE,
        ),
        rf"\1\2{MARK}\2",
    ),
    # SECRET_KEY=… in environment-file style (upper-case names, unquoted values)
    (
        re.compile(
            r"(^|\n)(\s*(?:export\s+)?[A-Z0-9_]*(?:PASSWORD|SECRET|TOKEN|API_KEY|PRIVATE_KEY|"
            r"ACCESS_KEY|CREDENTIALS)[A-Z0-9_]*\s*=\s*)(?!\[REDACTED|\$)([^\s'\"#][^\s#]{3,})"
        ),
        rf"\1\2{MARK}",
    ),
)


def redact(text: str) -> tuple[str, int]:
    """The text without secrets, and how many were removed."""
    total = 0
    for pattern, replacement in PATTERNS:
        text, count = pattern.subn(replacement, text)
        total += count
    return text, total


class RedactingProvider:
    """A provider whose prompts are redacted first (see the module docstring)."""

    def __init__(self, inner: LLMProvider) -> None:
        self._inner = inner
        self.name = inner.name

    def complete(self, request: LLMRequest) -> LLMResponse:
        system, in_system = redact(request.system)
        prompt, in_prompt = redact(request.prompt)
        if in_system or in_prompt:
            logger.info("llm_prompt_redacted", secrets=in_system + in_prompt)
        return self._inner.complete(replace(request, system=system, prompt=prompt))
