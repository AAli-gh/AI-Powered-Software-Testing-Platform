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


def test_secrets_are_not_leaked_by_repr() -> None:
    settings = make(github_webhook_secret="hook-value-1", github_private_key="-----BEGIN key")
    for text in (repr(settings), str(settings)):
        assert "user:secret@" not in text
        assert "hook-value-1" not in text
        assert "BEGIN key" not in text
    assert settings.database_url.get_secret_value() == VALID_DB
    assert settings.github_private_key_pem() == "-----BEGIN key"


def test_github_settings(tmp_path: Path) -> None:
    assert make().github_private_key_pem() is None
    key_file = tmp_path / "app.pem"
    key_file.write_text("-----BEGIN from file")
    settings = make(github_private_key_file=key_file, github_allowed_owners="acme, Other ,")
    assert settings.github_private_key_pem() == "-----BEGIN from file"
    assert settings.github_allowed_owners == ["acme", "Other"]
    one_line = make(github_private_key="-----BEGIN KEY-----\\nabc\\n-----END KEY-----")
    assert one_line.github_private_key_pem() == "-----BEGIN KEY-----\nabc\n-----END KEY-----"
    multi_line = make(github_private_key="-----BEGIN KEY-----\nabc\\n")
    assert multi_line.github_private_key_pem() == "-----BEGIN KEY-----\nabc\\n"
    with pytest.raises(ValidationError, match="github_webhook_secret"):
        make(github_webhook_secret="  ")


def test_empty_variables_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTMIND_GITHUB_APP_ID", "")
    monkeypatch.setenv("TESTMIND_GITHUB_WEBHOOK_SECRET", "")
    settings = make()
    assert (settings.github_app_id, settings.github_webhook_secret) == (None, None)


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
    monkeypatch.setenv("TESTMIND_API_TOKEN", "p" * 40)  # production requires one
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.env is Environment.PRODUCTION
        assert settings.repos_root == tmp_path.resolve()
        assert get_settings() is settings
    finally:
        get_settings.cache_clear()


def test_api_token_rules() -> None:
    assert make().api_token is None
    assert make(api_token="k" * 32).api_token is not None
    with pytest.raises(ValidationError, match="at least 32"):
        make(api_token="short")
    with pytest.raises(ValidationError, match="TESTMIND_API_TOKEN is required"):
        make(env="production")
    assert make(env="production", api_token="k" * 40).env == "production"
    assert "k" * 40 not in repr(make(api_token="k" * 40))


def test_sandbox_python_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    assert make().sandbox_python_versions == ["3.12"]
    assert make(sandbox_python_versions=" 3.8, 3.12 ,").sandbox_python_versions == ["3.8", "3.12"]
    monkeypatch.setenv("TESTMIND_SANDBOX_PYTHON_VERSIONS", "3.7,3.9")
    assert make().sandbox_python_versions == ["3.7", "3.9"]
    with pytest.raises(ValidationError, match=r"major\.minor: 3\.8\.1, 2\.7"):
        make(sandbox_python_versions="3.8.1,2.7")
