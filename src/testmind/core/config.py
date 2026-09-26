"""Application settings, loaded exclusively from environment variables (or a local `.env`)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


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
        frozen=True,
    )

    env: Environment = Environment.DEVELOPMENT
    log_level: LogLevel = "INFO"
    log_json: bool = True

    database_url: SecretStr
    database_pool_size: int = Field(default=5, ge=1, le=100)
    redis_url: SecretStr

    repos_root: Path

    celery_task_time_limit_seconds: int = Field(default=3600, ge=10)

    # --- AI test generation. The API key itself is NOT a TestMind setting: the Anthropic SDK
    # reads ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile) directly,
    # so the secret never passes through TestMind's configuration or logs.
    llm_provider: Literal["anthropic", "disabled"] = "anthropic"
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

    # --- Coverage: functions below this percentage are reported as coverage gaps.
    coverage_threshold: float = Field(default=80.0, ge=0, le=100)

    # --- AI failure analysis: failure groups explained per request (the rest are skipped).
    failure_analysis_max_groups: int = Field(default=10, ge=1, le=100)

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

    @field_validator("repos_root")
    @classmethod
    def _resolve_repos_root(cls, value: Path) -> Path:
        return value.expanduser().resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton (pydantic-settings reads the environment)."""
    return Settings()  # required fields are read from the environment
