from pathlib import Path

import pytest
from pydantic import ValidationError

from testmind.core.config import Environment, Settings, get_settings

VALID_DB = "postgresql+asyncpg://user:secret@db:5432/testmind"
VALID_REDIS = "redis://redis:6379/0"


def make(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": VALID_DB,
        "redis_url": VALID_REDIS,
        "repos_root": "/srv/repos",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = make()
    assert settings.env is Environment.DEVELOPMENT
    assert settings.log_level == "INFO"
    assert settings.log_json is True


def test_connection_urls_are_not_leaked_by_repr() -> None:
    settings = make()
    assert "secret" not in repr(settings)
    assert "secret" not in str(settings)
    assert settings.database_url.get_secret_value() == VALID_DB


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:pw@db/testmind",  # sync driver
        "postgresql+psycopg://user:pw@db/testmind",
        "sqlite+aiosqlite:///db.sqlite",
        "not a url",
    ],
)
def test_rejects_non_asyncpg_database_url(url: str) -> None:
    with pytest.raises(ValidationError, match="database_url"):
        make(database_url=url)


@pytest.mark.parametrize("url", ["http://redis:6379", "redis-host:6379"])
def test_rejects_invalid_redis_url(url: str) -> None:
    with pytest.raises(ValidationError, match="redis_url"):
        make(redis_url=url)


def test_accepts_tls_redis_url() -> None:
    assert make(redis_url="rediss://redis:6380/0").redis_url.get_secret_value().startswith("rediss")


def test_log_level_is_case_insensitive() -> None:
    assert make(log_level="debug").log_level == "DEBUG"


def test_rejects_unknown_log_level() -> None:
    with pytest.raises(ValidationError):
        make(log_level="verbose")


def test_repos_root_is_resolved_to_absolute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "repos").mkdir()
    assert make(repos_root="repos").repos_root == (tmp_path / "repos").resolve()


def test_settings_are_immutable() -> None:
    settings = make()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"  # type: ignore[misc]


def test_get_settings_reads_prefixed_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)  # make sure no developer .env is picked up
    monkeypatch.setenv("TESTMIND_DATABASE_URL", VALID_DB)
    monkeypatch.setenv("TESTMIND_REDIS_URL", VALID_REDIS)
    monkeypatch.setenv("TESTMIND_REPOS_ROOT", str(tmp_path))
    monkeypatch.setenv("TESTMIND_ENV", "production")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.env is Environment.PRODUCTION
        assert settings.repos_root == tmp_path.resolve()
        assert get_settings() is settings
    finally:
        get_settings.cache_clear()
