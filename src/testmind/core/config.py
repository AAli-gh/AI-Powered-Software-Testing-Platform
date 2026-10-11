"""Application settings, loaded exclusively from environment variables (or a local `.env`)."""

from __future__ import annotations

import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

MIN_TOKEN_LENGTH = 32
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class AIFeature(StrEnum):
    """The features that may call an external LLM. Detection never does (docs/ai-engine.md)."""

    TEST_GENERATION = "test_generation"
    FAILURE_EXPLANATION = "failure_explanation"
    MUTATION_SUGGESTIONS = "mutation_suggestions"
    API_SUGGESTIONS = "api_suggestions"


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Runtime configuration.

    Connection URLs are `SecretStr` because they usually embed credentials; they are never
    rendered by `repr()` and must be unwrapped explicitly with `.get_secret_value()`.
    """

    model_config = SettingsConfigDict(
        env_prefix="TESTMIND_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # `VAR=` (e.g. passed through by docker compose) means unset
        frozen=True,
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_json: bool = True

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1, le=100)
    redis_url: SecretStr

    repos_root: Path

    # --- API security (Phase 13). With a token, every /api/ request needs
    # `Authorization: Bearer <token>` (the GitHub webhook signs its deliveries instead).
    # Production refuses to start without one.
    api_token: SecretStr | None = None
    # State-changing requests per client and minute (0: unlimited).
    rate_limit_per_minute: int = Field(default=60, ge=0, le=100_000)

    celery_task_time_limit_seconds: int = Field(default=3600, ge=10)
    # Port on which the worker serves its Prometheus metrics (0: not served). Requires
    # PROMETHEUS_MULTIPROC_DIR (docs/monitoring.md).
    worker_metrics_port: int = Field(default=0, ge=0, le=65535)

    # --- External AI (an LLM), off by default: running tests, failure analysis and fault
    # localisation never use it. `anthropic` turns it on for the features in `ai_features`.
    # The API key itself is NOT a TestMind setting: the Anthropic SDK reads ANTHROPIC_API_KEY
    # (or ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile) directly, so the secret never
    # passes through TestMind's configuration or logs.
    llm_provider: Literal["anthropic", "disabled"] = "disabled"
    ai_features: Annotated[list[AIFeature], NoDecode] = Field(
        default_factory=lambda: list(AIFeature)
    )
    llm_model: str = Field(default="claude-opus-5", min_length=1, max_length=100)
    llm_max_tokens: int = Field(default=16000, ge=1024, le=128000)
    llm_timeout_seconds: float = Field(default=600.0, gt=0)
    generation_max_iterations: int = Field(default=3, ge=1, le=10)
    generation_max_targets: int = Field(default=10, ge=1, le=200)

    # --- Sandbox for running untrusted code (generated tests). See ADR-0003.
    sandbox_image: str = Field(default="testmind-sandbox:local", min_length=1, max_length=200)
    sandbox_timeout_seconds: int = Field(default=120, ge=5, le=3600)
    sandbox_memory: str = Field(default="512m", pattern=r"^[0-9]+[kmg]$")
    sandbox_cpus: float = Field(default=1.0, gt=0, le=16)
    sandbox_build_timeout_seconds: int = Field(default=900, ge=30, le=7200)
    # Python versions whose sandbox images exist (make sandbox-images): sandbox_image for 3.12,
    # `<its repository>:py<version>` for the others (sandbox/python_versions.py).
    sandbox_python_versions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["3.12"]
    )
    # Sandbox image with a headless Chromium for Playwright tests (docker/browser.Dockerfile).
    browser_sandbox_image: str | None = Field(default=None, min_length=1, max_length=200)

    # --- Coverage: functions below this percentage are reported as coverage gaps.
    coverage_threshold: float = Field(default=80.0, ge=0, le=100)

    # --- AI failure analysis: failure groups explained per request (the rest are skipped).
    failure_analysis_max_groups: int = Field(default=10, ge=1, le=100)

    # --- Mutation testing.
    mutation_max_mutants: int = Field(default=200, ge=1, le=5000)
    mutation_mutant_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    mutation_job_timeout_seconds: int = Field(default=1800, ge=60, le=14400)
    mutation_max_suggestions: int = Field(default=10, ge=1, le=100)
    api_max_suggestions: int = Field(default=10, ge=1, le=100)

    # --- GitHub App (Phase 11). The private key and the webhook secret are secrets: SecretStr,
    # from the environment only (the key may also be read from a file).
    github_app_id: int | None = Field(default=None, ge=1)
    github_private_key: SecretStr | None = None
    github_private_key_file: Path | None = None
    github_webhook_secret: SecretStr | None = None
    github_api_url: str = Field(default="https://api.github.com", pattern=r"^https?://")
    # Owners (users or organisations) whose repositories may be analysed; empty: any.
    github_allowed_owners: Annotated[list[str], NoDecode] = Field(default_factory=list)
    github_check_name: str = Field(default="TestMind", min_length=1, max_length=100)
    github_max_archive_mb: int = Field(default=200, ge=1, le=2000)
    # Where imported repositories are written (a directory of their own, never the repositories
    # root, which is mounted read-only). Unset: imports are disabled.
    github_imports_root: Path | None = None
    # The public URL of this API, for links in pull request comments (optional).
    public_url: str | None = Field(default=None, pattern=r"^https?://")

    def github_private_key_pem(self) -> str | None:
        """The App's private key, from the variable or the file; None when not configured."""
        if self.github_private_key is not None:
            return self.github_private_key.get_secret_value()
        if self.github_private_key_file is not None:
            return self.github_private_key_file.read_text("utf-8")
        return None

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = make_url(value.get_secret_value())
        except ArgumentError as exc:
            raise ValueError("database_url is not a valid SQLAlchemy URL") from exc
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("database_url must use the 'postgresql+asyncpg' driver")
        return value

    @field_validator("redis_url")
    @classmethod
    def _validate_redis_url(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith(("redis://", "rediss://")):
            raise ValueError("redis_url must start with redis:// or rediss://")
        return value

    @field_validator("github_private_key")
    @classmethod
    def _unescape_private_key(cls, value: SecretStr | None) -> SecretStr | None:
        """A PEM key on one line (`\\n` for its line breaks), as environment files need it."""
        if value is None:
            return None
        key = value.get_secret_value()
        return SecretStr(key.replace("\\n", "\n") if "\n" not in key else key)

    @field_validator("github_webhook_secret")
    @classmethod
    def _require_a_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("github_webhook_secret must not be blank")
        return value

    @field_validator("api_token")
    @classmethod
    def _strong_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_TOKEN_LENGTH:
            raise ValueError(
                f"api_token must have at least {MIN_TOKEN_LENGTH} characters "
                "(e.g. `openssl rand -hex 32`)"
            )
        return value

    @model_validator(mode="after")
    def _production_needs_a_token(self) -> Settings:
        if self.env is Environment.PRODUCTION and self.api_token is None:
            raise ValueError("TESTMIND_API_TOKEN is required when TESTMIND_ENV=production")
        return self

    @field_validator("github_allowed_owners", mode="before")
    @classmethod
    def _split_owners(cls, value: object) -> object:
        """`TESTMIND_GITHUB_ALLOWED_OWNERS=acme,other` (comma-separated)."""
        if isinstance(value, str):
            return [owner.strip() for owner in value.split(",") if owner.strip()]
        return value

    @field_validator("ai_features", mode="before")
    @classmethod
    def _split_features(cls, value: object) -> object:
        """`TESTMIND_AI_FEATURES=test_generation,failure_explanation` (comma-separated)."""
        if isinstance(value, str):
            return [feature.strip() for feature in value.split(",") if feature.strip()]
        return value

    @field_validator("sandbox_python_versions", mode="before")
    @classmethod
    def _split_versions(cls, value: object) -> object:
        """`TESTMIND_SANDBOX_PYTHON_VERSIONS=3.8,3.12` (comma-separated)."""
        if isinstance(value, str):
            return [version.strip() for version in value.split(",") if version.strip()]
        return value

    @field_validator("sandbox_python_versions")
    @classmethod
    def _version_format(cls, value: list[str]) -> list[str]:
        """major.minor only; which versions have images is checked by the image builder."""
        malformed = [v for v in value if not re.fullmatch(r"3\.\d{1,2}", v)]
        if malformed:
            raise ValueError(f"Python versions are written major.minor: {', '.join(malformed)}")
        return value

    @field_validator("repos_root")
    @classmethod
    def _resolve_repos_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (pydantic-settings reads the environment)."""
    return Settings()  # required fields are read from the environment
