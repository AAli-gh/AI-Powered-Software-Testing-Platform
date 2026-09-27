"""API coverage from static endpoints and a recorded capture. The captures here have the
exact shape the sandbox plugin writes; the real plugin is exercised in
tests/sandbox/test_api_capture.py."""

import json
from pathlib import Path
from typing import Any

import pytest

from testmind.analyzers.code import CodeAnalyzer
from testmind.analyzers.code.models import CodeProfile
from testmind.api_engine.capture import ApiCaptureError, RawApiCapture, parse_api_json
from testmind.api_engine.coverage import attribute_call, build_api_coverage
from testmind.api_engine.models import (
    ApiSurface,
    Endpoint,
    Framework,
    Handler,
    PathParam,
    Source,
)
from tests.unit.analyzers.helpers import make_repo

ROOT = "/workspace/src"


def code_profile(tmp_path: Path, files: dict[str, str]) -> CodeProfile:
    return CodeAnalyzer().analyze(make_repo(tmp_path, files), list(files))


TEST_FILE = "/workspace/src/tests/test_api.py"


def endpoint(
    method: str,
    path: str,
    *,
    framework: Framework = Framework.FASTAPI,
    body: bool = False,
    qualname: str | None = None,
) -> Endpoint:
    params = [PathParam(name=p.strip("{}"), type="int") for p in path.split("/") if "{" in p]
    handler = Handler(module="app", qualname=qualname, path="app.py", line=1) if qualname else None
    return Endpoint(
        framework=framework, method=method, path=path, params=params, handler=handler,
        accepts_body=body, app="app:app",
    )  # fmt: skip


def call(
    method: str,
    route: str | None,
    status: int | None,
    *,
    test: str = "test_a",
    path: str | None = None,
    file: str | None = TEST_FILE,
    framework: str = "fastapi",
    exception: bool = False,
    count: int = 1,
) -> dict[str, Any]:
    return {
        "test_path": file, "test_name": test, "framework": framework, "method": method,
        "route": route, "path": path or route or "/", "status": status, "exception": exception,
        "count": count,
    }  # fmt: skip


def capture(
    calls: list[dict[str, Any]], routes: list[tuple[str, list[str]]] | None = None, **extra: Any
) -> RawApiCapture:
    apps = []
    if routes is not None:
        apps = [
            {
                "framework": "fastapi",
                "routes": [
                    {"path": p, "methods": m, "handler": "app:f", "internal": False}
                    for p, m in routes
                ]
                + [{"path": "/docs", "methods": ["GET"], "handler": "fastapi:x", "internal": True}],
            }
        ]
    document = {"version": 1, "rootdir": ROOT, "calls": calls, "apps": apps, **extra}
    return parse_api_json(json.dumps(document))


SURFACE = ApiSurface(
    endpoints=[
        endpoint("GET", "/items", qualname="list_items"),
        endpoint("POST", "/items", body=True, qualname="create_item"),
        endpoint("GET", "/items/{id}", qualname="get_item"),
        endpoint("DELETE", "/items/{id}", qualname="delete_item"),
        endpoint("GET", "/health", qualname="health"),
    ]
)
ROUTES = [
    ("/items", ["GET"]),
    ("/items", ["POST"]),
    ("/items/{id}", ["GET"]),
    ("/items/{id}", ["DELETE"]),
    ("/health", ["GET"]),
]


def test_requests_are_attributed_to_endpoints() -> None:
    recorded = capture(
        [
            call("GET", "/items", 200, count=2),
            call("POST", "/items", 201, test="test_b"),
            call("POST", "/items", 422, test="test_b"),
            call("GET", "/items/{id}", 404, path="/items/9"),
            call("GET", "/items/{id}", 500, path="/items/1", exception=True, test="test_c"),
            call("GET", "/nope", 404, path="/nope"),  # unmatched: no route answered
            call("GET", None, 404, path="/missing"),
            call("GET", "/health", 200, file="/workspace/tests/testmind/test_x.py"),
            call("GET", "/typo", 404, file="/workspace/tests/testmind/test_x.py"),  # not listed
        ],
        ROUTES,
    )
    report = build_api_coverage(SURFACE, recorded)
    by_key = {f"{e.method} {e.path}": e for e in report.endpoints}

    items = by_key["GET /items"]
    assert (items.calls, items.tests, items.statuses) == (
        2,
        ["tests/test_api.py::test_a"],
        {"200": 2},
    )
    assert (items.source, items.tested, items.success_tested, items.error_tested) == (
        Source.BOTH, True, True, False,
    )  # fmt: skip
    create = by_key["POST /items"]
    assert (create.statuses, create.error_tested) == ({"201": 1, "422": 1}, True)
    get_item = by_key["GET /items/{id}"]
    assert (get_item.statuses, get_item.server_errors) == ({"404": 1, "500": 1}, 1)
    health = by_key["GET /health"]  # only a generated test called it
    assert (health.tested, health.generated_calls, health.calls) == (False, 1, 0)
    assert [(u.method, u.path, u.status, u.tests) for u in report.unmatched] == [
        ("GET", "/missing", 404, ["tests/test_api.py::test_a"]),
        ("GET", "/nope", 404, ["tests/test_api.py::test_a"]),
    ]
    assert report.totals.model_dump() == {
        "endpoints": 5, "tested": 3, "success_tested": 2, "error_tested": 2, "percent": 60.0,
    }  # fmt: skip
    assert report.frameworks == [Framework.FASTAPI]
    assert report.notes == []


def test_gaps_are_ranked_by_kind_then_complexity(tmp_path: Path) -> None:
    recorded = capture(
        [
            call("GET", "/items", 200),
            call("POST", "/items", 201),
            call("GET", "/items/{id}", 500, path="/items/1"),
            call("DELETE", "/items/{id}", 404, path="/items/1"),
        ],
        ROUTES,
    )
    code = code_profile(
        tmp_path,
        {
            "app.py": (
                "def health():\n    return 1\n\n"
                "def list_items(x):\n    if x:\n        return 1\n    return 2\n"
            )
        },
    )
    report = build_api_coverage(SURFACE, recorded, code=code)
    assert [(g.kind, g.method, g.path, g.complexity) for g in report.gaps] == [
        ("server_error", "GET", "/items/{id}", None),
        ("untested", "GET", "/health", 1),
        ("error_only", "DELETE", "/items/{id}", None),
        ("success_only", "POST", "/items", None),  # a body to validate, never rejected
    ]  # GET /items takes no input: tested successfully is enough
    assert report.gaps[0].reason == "1 request(s) answered with a server error (5xx)"


def test_static_and_runtime_endpoints_are_cross_checked() -> None:
    surface = ApiSurface(
        endpoints=[
            endpoint("GET", "/a"),
            endpoint("GET", "/only-static"),
            endpoint("ANY", "/django/", framework=Framework.DJANGO),
            endpoint("GET", "/specific/", framework=Framework.DJANGO),
        ]
    )
    recorded = parse_api_json(
        json.dumps(
            {
                "version": 1,
                "rootdir": ROOT,
                "calls": [
                    call("PATCH", "django/", 200, framework="django"),
                    call("GET", "/x", 200, framework="bottle"),  # not a supported framework
                ],
                "apps": [
                    {
                        "framework": "fastapi",
                        "routes": [
                            {"path": "/a", "methods": ["GET"]},
                            {"path": "/dynamic/{n}", "methods": ["PUT"]},
                        ],
                    },
                    {
                        "framework": "django",
                        "routes": [
                            {"path": "django/", "methods": ["ANY"]},
                            {"path": "specific/", "methods": ["ANY"]},
                        ],
                    },
                    {"framework": "bottle", "routes": []},
                ],
            }
        )
    )
    report = build_api_coverage(surface, recorded)
    assert [(e.method, e.path, e.source) for e in report.endpoints] == [
        ("ANY", "/django/", Source.BOTH),
        ("GET", "/specific/", Source.BOTH),  # static knows the method, runtime says ANY
        ("GET", "/a", Source.BOTH),
        ("PUT", "/dynamic/{n}", Source.RUNTIME),
        ("GET", "/only-static", Source.STATIC),
    ]
    django = report.endpoints[0]
    assert (django.calls, django.statuses) == (1, {"200": 1})
    assert report.notes == [
        "API capture: unknown framework 'bottle'",
        "1 endpoint(s) found statically are not registered by the running application: "
        "GET /only-static",
        "1 endpoint(s) registered at run time were not found statically: PUT /dynamic/{n}",
    ]


def test_notes_when_nothing_was_recorded() -> None:
    no_capture = build_api_coverage(SURFACE, None, notes=["The plugin wrote nothing"])
    assert no_capture.notes == [
        "The plugin wrote nothing",
        "No route table was recorded: the endpoints come from static discovery only",
    ]
    assert no_capture.totals.tested == 0
    silent = build_api_coverage(SURFACE, capture([], None, dropped=3, errors=["flask: boom"]))
    assert silent.notes == [
        "No test sent a request to the application, so its route table was not recorded: "
        "the endpoints come from static discovery only",
        "3 request record(s) beyond the capture limit",
        "API capture: flask: boom",
    ]
    empty = build_api_coverage(ApiSurface(), None)
    assert (empty.totals.percent, empty.endpoints, empty.notes) == (None, [], [])


def test_many_mismatches_are_listed_with_a_count() -> None:
    surface = ApiSurface(endpoints=[endpoint("GET", f"/s{i}") for i in range(7)])
    report = build_api_coverage(surface, capture([], [("/other", ["GET"])]))
    assert report.notes[0] == (
        "7 endpoint(s) found statically are not registered by the running application: "
        "GET /s0, GET /s1, GET /s2, GET /s3, GET /s4 (+2 more)"
    )


def test_calls_outside_a_test_or_the_workspace() -> None:
    recorded = capture(
        [
            call("GET", "/items", 200, test=None, file=None),  # type: ignore[arg-type]
            call("GET", "/items", 200, test="t", file="/usr/lib/python3/x.py"),
            call("GET", "/items", None),  # no status: the response never started
        ],
        ROUTES,
    )
    items = build_api_coverage(SURFACE, recorded).endpoints[1]
    assert (items.path, items.tests) == (
        "/items", ["<outside a test>", "t", "tests/test_api.py::test_a"],
    )  # fmt: skip
    assert items.statuses == {"200": 2, "None": 1}


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            '{"version": 2, "rootdir": "/", "calls": [], "apps": []}',
            "Unsupported API capture version 2",
        ),
        ('{"calls": "x"}', "Not a TestMind API capture"),
        ("not json", "Not a TestMind API capture"),
    ],
)
def test_invalid_captures(document: str, message: str) -> None:
    with pytest.raises(ApiCaptureError, match=message):
        parse_api_json(document)


def test_capture_strings_are_bounded() -> None:
    with pytest.raises(ApiCaptureError):
        capture([call("GET", "/x" * 1000, 200)])


def test_code_profile_default_is_an_empty_surface(tmp_path: Path) -> None:
    profile = code_profile(tmp_path, {"lib.py": "x = 1\n"})
    assert isinstance(profile, CodeProfile)
    assert profile.api == ApiSurface()


def test_a_request_of_an_unknown_framework_is_not_attributed() -> None:
    [record] = capture([call("GET", "/items", 200, framework="bottle")]).calls
    assert attribute_call(record, [endpoint("GET", "/items")]) is None
