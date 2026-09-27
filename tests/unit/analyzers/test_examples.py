"""Golden tests: the analyzer against the real example repositories in examples/."""

from pathlib import Path

import pytest

from testmind.analyzers.repository import RepositoryAnalyzer, RepositoryProfile
from testmind.analyzers.repository.models import DependencyGroup, EntryPointKind

EXAMPLES = Path(__file__).resolve().parents[3] / "examples"


@pytest.fixture(scope="module")
def profiles() -> dict[str, RepositoryProfile]:
    return {
        name: RepositoryAnalyzer().analyze(EXAMPLES / name)
        for name in (
            "discount_service",
            "django_blog",
            "fastapi_todo",
            "flask_notes",
            "web_counter",
        )
    }


def detections(profile: RepositoryProfile) -> dict[str, float]:
    return {d.name: d.confidence for d in profile.frameworks + profile.test_frameworks}


def deps(profile: RepositoryProfile, group: DependencyGroup) -> set[str]:
    return {d.name for d in profile.dependencies if d.group is group}


def entry_points(profile: RepositoryProfile) -> set[tuple[EntryPointKind, str | None]]:
    return {(e.kind, e.target) for e in profile.entry_points}


def test_every_example_is_clean(profiles: dict[str, RepositoryProfile]) -> None:
    for name, profile in profiles.items():
        assert profile.issues == [], name
        assert profile.primary_language == "Python", name
        assert profile.truncated is False, name


def test_django_blog(profiles: dict[str, RepositoryProfile]) -> None:
    profile = profiles["django_blog"]
    assert detections(profile) == {"Django": 1.0, "unittest": 0.7, "pytest": 0.5}
    assert deps(profile, DependencyGroup.RUNTIME) == {"django", "djangorestframework", "psycopg"}
    assert deps(profile, DependencyGroup.DEVELOPMENT) == {"pytest", "pytest-django"}
    assert profile.test_files == ["posts/tests.py"]
    assert entry_points(profile) == {
        (EntryPointKind.DJANGO_MANAGE, None),
        (EntryPointKind.WSGI_APP, "blog_project.wsgi:application"),
        (EntryPointKind.ASGI_APP, "blog_project.asgi:application"),
    }
    assert profile.docker.base_images == ["python:3.12-slim"]
    assert "db.sqlite3" not in {f.path for f in profile.files}  # would be .gitignored anyway
    assert profile.django_settings_module == "blog_project.settings"


def test_fastapi_todo(profiles: dict[str, RepositoryProfile]) -> None:
    profile = profiles["fastapi_todo"]
    assert detections(profile) == {"FastAPI": 1.0, "pytest": 1.0}
    assert profile.django_settings_module is None
    assert deps(profile, DependencyGroup.RUNTIME) == {"fastapi", "uvicorn", "pydantic"}
    assert deps(profile, DependencyGroup.OPTIONAL) == {"asyncpg"}
    assert deps(profile, DependencyGroup.DEVELOPMENT) == {"pytest", "httpx"}
    assert profile.test_files == ["tests/test_items.py"]
    assert entry_points(profile) == {
        (EntryPointKind.ASGI_APP, "todo.main:app"),
        (EntryPointKind.CONSOLE_SCRIPT, "todo-serve = todo.cli:main"),
        (EntryPointKind.SCRIPT_MAIN, None),
    }
    assert profile.docker.compose_services == ["api", "db"]
    assert [(c.provider, c.jobs) for c in profile.ci] == [("github_actions", ["lint", "test"])]


def test_flask_notes(profiles: dict[str, RepositoryProfile]) -> None:
    profile = profiles["flask_notes"]
    assert detections(profile) == {"Flask": 1.0, "unittest": 1.0, "pytest": 0.5}
    assert deps(profile, DependencyGroup.RUNTIME) == {"flask", "sqlalchemy"}
    assert entry_points(profile) == {
        (EntryPointKind.WSGI_APP, "notes:create_app()"),
        (EntryPointKind.CONSOLE_SCRIPT, "notes-admin = notes.admin:run"),
    }
    assert [(c.provider, c.jobs) for c in profile.ci] == [("gitlab_ci", ["unit-tests"])]


def test_discount_service(profiles: dict[str, RepositoryProfile]) -> None:
    profile = profiles["discount_service"]
    assert detections(profile) == {"pytest": 0.7}
    assert profile.dependencies == []
    assert profile.test_files == ["tests/test_pricing.py"]
    assert profile.frameworks == []


def test_no_web_framework_false_positives_across_examples(
    profiles: dict[str, RepositoryProfile],
) -> None:
    web = {name: {d.name for d in p.frameworks} for name, p in profiles.items()}
    assert web == {
        "discount_service": set(),
        "django_blog": {"Django"},
        "fastapi_todo": {"FastAPI"},
        "flask_notes": {"Flask"},
        "web_counter": {"Flask"},
    }


def test_web_counter_has_browser_tests(profiles: dict[str, RepositoryProfile]) -> None:
    profile = profiles["web_counter"]
    assert detections(profile) == {"Flask": 1.0, "Playwright": 1.0, "pytest": 1.0}
    [playwright] = [d for d in profile.test_frameworks if d.name == "Playwright"]
    assert playwright.evidence == [
        "'pytest-playwright' declared in pyproject.toml",
        "'playwright' declared in pyproject.toml",
        "'playwright' imported in 1 test file(s): tests/test_counter.py",
    ]
