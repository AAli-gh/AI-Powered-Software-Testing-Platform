"""Golden tests: the endpoints of the example web applications, exactly."""

from pathlib import Path

import pytest

from testmind.analyzers.code import CodeAnalyzer
from testmind.analyzers.repository import RepositoryAnalyzer
from testmind.api_engine.models import ApiSurface

EXAMPLES = Path(__file__).parents[3] / "examples"


def api_of(name: str) -> ApiSurface:
    root = EXAMPLES / name
    repository = RepositoryAnalyzer().analyze(root)
    python = [f.path for f in repository.files if f.language == "Python"]
    return CodeAnalyzer().analyze(root, python, repository.test_files).api


@pytest.mark.parametrize(
    ("example", "target", "endpoints"),
    [
        (
            "fastapi_todo",
            "todo.main:app",
            [
                ("GET", "/health", "health", False),
                ("GET", "/items/", "list_items", False),
                ("POST", "/items/", "create_item", True),
                ("DELETE", "/items/{item_id}", "delete_item", False),
            ],
        ),
        (
            "flask_notes",
            "notes:create_app()",
            [
                ("GET", "/notes", "create_app.<locals>.list_notes", False),
                ("POST", "/notes", "create_app.<locals>.add_note", True),
            ],
        ),
        (
            "django_blog",
            None,
            [
                ("ANY", "/api/posts/", "post_list", False),
                ("ANY", "/api/posts/{pk}/", "post_detail", False),
            ],
        ),
    ],
)
def test_example_endpoints(
    example: str, target: str | None, endpoints: list[tuple[str, str, str, bool]]
) -> None:
    api = api_of(example)
    [app] = api.apps
    assert app.target == target
    assert [
        (e.method, e.path, e.handler.qualname if e.handler else None, e.accepts_body)
        for e in api.endpoints
    ] == endpoints
    assert api.notes == []


@pytest.mark.parametrize("example", ["discount_service", "buggy_inventory", "shipping_rates"])
def test_projects_without_a_web_framework_have_no_endpoints(example: str) -> None:
    assert api_of(example) == ApiSurface()
