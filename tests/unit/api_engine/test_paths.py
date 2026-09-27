import pytest

from testmind.api_engine.models import Framework
from testmind.api_engine.paths import canonical, join, matches


@pytest.mark.parametrize(
    ("framework", "template", "path", "params"),
    [
        (Framework.FASTAPI, "/items/{item_id}", "/items/{item_id}", [("item_id", "unknown")]),
        (Framework.FASTAPI, "/files/{p:path}", "/files/{p}", [("p", "path")]),
        (Framework.FASTAPI, "health", "/health", []),
        (Framework.FLASK, "/notes/<int:id>", "/notes/{id}", [("id", "int")]),
        (
            Framework.FLASK,
            "/u/<name>/<path:rest>",
            "/u/{name}/{rest}",
            [("name", "str"), ("rest", "path")],
        ),
        (Framework.FLASK, "/c/<custom(x=1):v>", "/c/{v}", [("v", "unknown")]),
        (Framework.DJANGO, "api/posts/<int:pk>/", "/api/posts/{pk}/", [("pk", "int")]),
        (Framework.DJANGO, "^api/^(?P<pk>[0-9]+)/$", "/api/{pk}/", [("pk", "int")]),
        (Framework.DJANGO, r"^tag/(?P<name>[^/]+)/$", "/tag/{name}/", [("name", "regex")]),
        (Framework.DJANGO, r"^a/(?P<x>(ab|cd))/$", "/a/{x}/", [("x", "regex")]),
        (Framework.DJANGO, "", "/", []),
    ],
)
def test_canonical(
    framework: Framework, template: str, path: str, params: list[tuple[str, str]]
) -> None:
    result, found = canonical(framework, template)
    assert result == path
    assert [(p.name, p.type) for p in found] == params


@pytest.mark.parametrize(
    ("prefix", "path", "joined"),
    [("", "/x", "/x"), ("/v1", "", "/v1"), ("/v1/", "/x", "/v1/x"), ("/v1", "/x", "/v1/x")],
)
def test_join(prefix: str, path: str, joined: str) -> None:
    assert join(prefix, path) == joined


def test_matches() -> None:
    assert matches("/items/{id}", "/items/7")
    assert not matches("/items/{id}", "/items/7/x")
    assert not matches("/items/{id}", "/items/")
    assert matches("/a.b/{x}", "/a.b/1")
    assert not matches("/a.b/{x}", "/aXb/1")
