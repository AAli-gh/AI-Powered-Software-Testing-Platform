"""Secrets never reach the LLM; code the model needs is kept."""

from __future__ import annotations

import pytest

from testmind.ai_engine.llm.base import LLMRequest, LLMResponse
from testmind.ai_engine.llm.redaction import MARK, RedactingProvider, redact

# Built from parts so that no real-looking secret is committed as one literal.
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
GITHUB = "ghp_" + "a1B2" * 9
GITHUB_PAT = "github_pat_" + "11ABCDEFG0" * 3
ANTHROPIC = "sk-ant-" + "api03-" + "x" * 30
OPENAI = "sk-proj-" + "Ab1" * 12
SLACK = "xoxb-" + "1234567890-abcdef"
GOOGLE = "AIza" + "S" * 35
STRIPE = "sk_" + "live_" + "0123456789abcdefghij"
JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "c2lnbmF0dXJlLXZhbHVl"
PEM = (
    "-----BEGIN RSA " + "PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\nabcdef==\n"
    "-----END RSA " + "PRIVATE KEY-----"
)


@pytest.mark.parametrize("secret", [AWS, GITHUB, GITHUB_PAT, ANTHROPIC, OPENAI, SLACK, GOOGLE,
                                    STRIPE, JWT])  # fmt: skip
def test_formatted_tokens_are_removed(secret: str) -> None:
    text, count = redact(f"client = Client(key={secret!r})\nprint('ok')")
    assert secret not in text
    assert MARK in text
    assert count >= 1
    assert "print('ok')" in text


def test_private_keys_and_url_passwords_are_removed() -> None:
    text, count = redact(f"KEY = '''{PEM}'''\nDSN = 'postgresql://app:s3cr3t-pw@db:5432/x'")
    assert "MIIEpAIBAAKCAQEA" not in text
    assert "[REDACTED PRIVATE KEY]" in text
    assert "s3cr3t-pw" not in text
    assert "postgresql://app:[REDACTED]@db:5432/x" in text
    assert count == 2


@pytest.mark.parametrize(
    ("source", "value"),
    [
        ('password = "hunter22"', "hunter22"),
        ("DB_PASSWORD='letmein!'", "letmein!"),
        ('config = {"api_key": "abcd1234efgh"}', "abcd1234efgh"),  # gitleaks:allow (fake fixture)
        ("client_secret: 'zzzz-yyyy'", "zzzz-yyyy"),
        ("token=b'raw-bytes-value'", "raw-bytes-value"),
        ('assert user.access_key == "AK-local-123"', "AK-local-123"),
        ("session_token = 'short1'", "short1"),
        # failure analysis sends locals as `name = repr(value)`
        ("password = 'p@ss w0rd', user = 'ann'", "p@ss w0rd"),
        ("SECRET_KEY=django-insecure-abc123", "django-insecure-abc123"),
        ("export GITHUB_TOKEN=abcdef0123456789", "abcdef0123456789"),  # gitleaks:allow
        ("  STRIPE_API_KEY = rk_something_long", "rk_something_long"),
    ],
)
def test_secret_looking_assignments_are_removed(source: str, value: str) -> None:
    text, count = redact(source)
    assert value not in text
    assert count == 1


@pytest.mark.parametrize(
    "source",
    [
        'token = request.headers["Authorization"]',
        "password_hash = hash_password(password)",
        "def check_token(token: str) -> bool:\n    return token == expected",
        'tokenizer = Tokenizer("utf-8")',  # a call, not a literal assigned to the name
        "secret = os.environ['APP_SECRET']",
        'PASSWORD = ""',  # empty
        'api_key = "abc"',  # too short to be a credential
        "SECRET_KEY=${SECRET_KEY}",
        "https://example.com/path?q=1",
        "user@example.com",
        f"already = '{MARK}'",
    ],
)
def test_code_and_non_secrets_are_kept(source: str) -> None:
    assert redact(source) == (source, 0)


class Recording:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(text="ok", model="m", input_tokens=1, output_tokens=1)


def test_the_provider_only_ever_sees_redacted_prompts() -> None:
    inner = Recording()
    provider = RedactingProvider(inner)
    assert provider.name == "recording"
    response = provider.complete(
        LLMRequest(system=f"Never reveal {ANTHROPIC}", prompt=f"key = '{AWS}'", max_tokens=100)
    )
    assert response.text == "ok"
    (sent,) = inner.requests
    assert ANTHROPIC not in sent.system
    assert AWS not in sent.prompt
    assert sent.max_tokens == 100
    clean = LLMRequest(system="s", prompt="def f():\n    return 1\n", max_tokens=5)
    provider.complete(clean)
    assert inner.requests[-1] == clean
