"""One canonical form for route templates of every framework.

FastAPI `/items/{item_id}` and `/files/{p:path}`, Flask `/notes/<int:id>` and Django
`api/posts/<int:pk>/` or `^api/(?P<pk>[0-9]+)/$` all become `/…/{name}`. Static discovery,
the runtime route table and recorded requests are compared in this form.
"""

from __future__ import annotations

import re

from testmind.api_engine.models import Framework, PathParam

_ANGLE = re.compile(r"<(?:(?P<conv>[A-Za-z_][\w]*)(?:\([^)>]*\))?:)?(?P<name>[A-Za-z_]\w*)>")
_BRACE = re.compile(r"\{(?P<name>[A-Za-z_]\w*)(?::(?P<conv>[A-Za-z_]\w*))?\}")
_REGEX_GROUP = re.compile(r"\(\?P<(?P<name>[A-Za-z_]\w*)>(?P<body>(?:[^()]|\([^()]*\))*)\)")
_NUMERIC_BODIES = frozenset({"[0-9]+", r"\d+", "[0-9]{1,}", r"\d{1,}"})

FLASK_TYPES = {"int": "int", "float": "float", "path": "path", "uuid": "uuid", "string": "str",
               "any": "str"}  # fmt: skip
DJANGO_TYPES = {"int": "int", "str": "str", "slug": "slug", "uuid": "uuid", "path": "path"}


def canonical(framework: Framework, template: str) -> tuple[str, list[PathParam]]:
    """(canonical path, parameters in order) for a route template of `framework`."""
    params: list[PathParam] = []
    path = template

    if framework is Framework.DJANGO:
        path = _strip_anchors(path)

        def regex(match: re.Match[str]) -> str:
            body = match.group("body")
            kind = "int" if body in _NUMERIC_BODIES else "regex"
            params.append(PathParam(name=match.group("name"), type=kind))
            return "{" + match.group("name") + "}"

        path = _REGEX_GROUP.sub(regex, path)

    if framework in {Framework.FLASK, Framework.DJANGO}:
        types = FLASK_TYPES if framework is Framework.FLASK else DJANGO_TYPES
        default = "str"

        def angle(match: re.Match[str]) -> str:
            conv = match.group("conv")
            kind = default if conv is None else types.get(conv, "unknown")
            params.append(PathParam(name=match.group("name"), type=kind))
            return "{" + match.group("name") + "}"

        path = _ANGLE.sub(angle, path)
    else:

        def brace(match: re.Match[str]) -> str:
            conv = match.group("conv")
            params.append(PathParam(name=match.group("name"), type=conv or "unknown"))
            return "{" + match.group("name") + "}"

        path = _BRACE.sub(brace, path)

    if not path.startswith("/"):
        path = "/" + path
    return path, params


def join(prefix: str, path: str) -> str:
    """Concatenate a router prefix and a route path the way the frameworks do."""
    if not prefix:
        return path
    if not path:
        return prefix
    if prefix.endswith("/") and path.startswith("/"):
        return prefix + path[1:]
    return prefix + path


def _strip_anchors(route: str) -> str:
    """Django regex routes: `^api/` + `^(?P<pk>…)/$` → `api/(?P<pk>…)/`."""
    route = re.sub(r"(?:^|(?<=/))\^", "", route)  # `[^/]` inside a class is kept
    return re.sub(r"(?<!\\)\$$", "", route)


def matches(template: str, path: str) -> bool:
    """Does a concrete request path fit a canonical template?"""
    pattern = "^" + re.sub(r"\\\{[A-Za-z_]\w*\\\}", "[^/]+", re.escape(template)) + "$"
    return re.match(pattern, path) is not None
