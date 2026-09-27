"""Probe planning, the generated module and the verdicts. The probes run for real in
tests/sandbox/test_api_testing.py."""

import ast
import json

import pytest

from testmind.api_engine.capture import RawApiCapture, parse_api_json
from testmind.api_engine.models import (
    ApiApp,
    ApiSurface,
    Endpoint,
    Framework,
    PathParam,
    ProbeKind,
    ProbeVerdict,
)
from testmind.api_engine.probes import PROBE_FILE, ProbePlan, evaluate_probes, plan_probes
from testmind.sandbox.models import Outcome
from testmind.test_engine.models import TestCaseReport

FASTAPI_APP = ApiApp(
    framework=Framework.FASTAPI, module="shop.main", path="src/shop/main.py", line=3,
    variable="app", factory=None,
)  # fmt: skip
FLASK_APP = ApiApp(
    framework=Framework.FLASK, module="notes", path="notes/__init__.py", line=5,
    variable=None, factory="create_app",
)  # fmt: skip


def endpoint(
    method: str,
    path: str,
    *,
    app: ApiApp = FASTAPI_APP,
    body: bool = False,
    params: dict[str, str] | None = None,
) -> Endpoint:
    return Endpoint(
        framework=app.framework, method=method, path=path,
        params=[PathParam(name=n, type=t) for n, t in (params or {}).items()], handler=None,
        accepts_body=body, app=app.target,
    )  # fmt: skip


SHOP = ApiSurface(
    apps=[FASTAPI_APP],
    endpoints=[
        endpoint("GET", "/items"),
        endpoint("POST", "/items", body=True),
        endpoint("GET", "/items/{id}", params={"id": "int"}),
        endpoint("PUT", "/items/{id}", params={"id": "int"}, body=True),
        endpoint("GET", "/items/{id}/tags/{tag}", params={"id": "int", "tag": "str"}),
        endpoint("GET", "/files/{key}", params={"key": "uuid"}),
    ],
)


def test_planned_probes() -> None:
    plan = plan_probes(SHOP)
    assert [(p.test, p.request, p.expected, p.method, p.path) for p in plan.probes] == [
        ("test_probe_001_unknown_path", "GET /__testmind_probe__/no-such-path", "404", "GET",
         "/__testmind_probe__/no-such-path"),
        ("test_probe_002_wrong_method", "DELETE /files/00000000-0000-4000-8000-000000000001",
         "405", "GET", "/files/{key}"),
        ("test_probe_003_wrong_method", "DELETE /items", "405", "GET/POST", "/items"),
        ("test_probe_004_wrong_method", "DELETE /items/1", "405", "GET/PUT", "/items/{id}"),
        ("test_probe_005_wrong_method", "DELETE /items/1/tags/probe", "405", "GET",
         "/items/{id}/tags/{tag}"),
        ("test_probe_006_empty_body", "POST /items with body {}", "4xx", "POST", "/items"),
        ("test_probe_007_malformed_body", 'POST /items with body {"unterminated', "4xx", "POST",
         "/items"),
        ("test_probe_008_invalid_param", "GET /items/not-a-number", "4xx", "GET", "/items/{id}"),
        ("test_probe_009_invalid_param", "PUT /items/not-a-number", "4xx", "PUT", "/items/{id}"),
        ("test_probe_010_empty_body", "PUT /items/1 with body {}", "4xx", "PUT", "/items/{id}"),
        ("test_probe_011_malformed_body", 'PUT /items/1 with body {"unterminated', "4xx", "PUT",
         "/items/{id}"),
        ("test_probe_012_invalid_param", "GET /items/not-a-number/tags/probe", "4xx", "GET",
         "/items/{id}/tags/{tag}"),
    ]  # fmt: skip
    assert plan.notes == []


def test_the_generated_module() -> None:
    plan = plan_probes(
        ApiSurface(
            apps=[FASTAPI_APP, FLASK_APP],
            endpoints=[
                endpoint("POST", "/items", body=True),
                endpoint("GET", "/notes/{id}", app=FLASK_APP, params={"id": "int"}),
                endpoint("POST", "/notes", app=FLASK_APP, body=True),
            ],
        )
    )
    assert plan.module is not None
    ast.parse(plan.module)
    assert plan.module.startswith(
        '"""Deterministic API probes written by TestMind from a template (no LLM)."""\n\n'
        "from fastapi.testclient import TestClient\n"
        "from notes import create_app as _source_1\n"
        "from shop.main import app as _source_0\n\n"
        "client_0 = TestClient(_source_0)\n"
        "_app_1 = _source_1()\n"
        "_app_1.testing = True  # exceptions reach the test with their traceback\n"
        "client_1 = _app_1.test_client()\n"
    )
    assert (
        "def test_probe_003_empty_body():\n"
        '    """empty_body: POST /items with body {} must be answered 4xx."""\n'
        "    response = client_0.request('POST', '/items', content='{}', "
        "headers={'Content-Type': 'application/json'})\n"
        "    assert 400 <= response.status_code < 500, response.status_code\n"
    ) in plan.module
    assert (
        "def test_probe_006_wrong_method():\n"
        '    """wrong_method: DELETE /notes must be answered 405."""\n'
        "    response = client_1.open('/notes', method='DELETE')\n"
        "    assert response.status_code == 405, response.status_code\n"
    ) in plan.module


def test_what_is_not_probed() -> None:
    django = ApiApp(
        framework=Framework.DJANGO, module="site.urls", path="site/urls.py", line=1,
        variable=None, factory=None,
    )  # fmt: skip
    needs_arguments = FLASK_APP.model_copy(update={"factory_needs_arguments": True})
    plan = plan_probes(
        ApiSurface(
            apps=[django, needs_arguments],
            endpoints=[endpoint("ANY", "/x/", app=django)],
        )
    )
    assert plan == ProbePlan(
        [],
        None,
        [
            "django application site.urls is not probed",
            "flask application in notes/__init__.py:5 is not probed: it is created by a "
            "factory that needs arguments",
        ],
    )


def test_probes_that_another_route_would_answer_are_skipped() -> None:
    plan = plan_probes(
        ApiSurface(
            apps=[FASTAPI_APP],
            endpoints=[
                # catches the unknown path
                endpoint("GET", "/{section}/{page}", params={"section": "str", "page": "str"}),
                endpoint("GET", "/items/{id}", params={"id": "int"}),
                endpoint("GET", "/items/{slug}", params={"slug": "str"}),  # catches "not-a-number"
                endpoint("DELETE", "/items/{slug}", params={"slug": "str"}),
                endpoint("PUT", "/items/{slug}", params={"slug": "str"}),
                endpoint("PATCH", "/items/{slug}", params={"slug": "str"}),
                endpoint("POST", "/items/{slug}", params={"slug": "str"}),
            ],
        )
    )
    assert [p.request for p in plan.probes] == ["DELETE /probe/probe"]


def test_the_limit() -> None:
    surface = ApiSurface(
        apps=[FASTAPI_APP], endpoints=[endpoint("GET", f"/r{i}") for i in range(5)]
    )
    plan = plan_probes(surface, limit=3)
    assert len(plan.probes) == 3
    assert plan.notes == ["6 probes planned; the first 3 were run"]


def recorded(*calls: tuple[str, int | None, bool]) -> RawApiCapture:
    return parse_api_json(
        json.dumps(
            {
                "version": 1,
                "rootdir": "/workspace/src",
                "calls": [
                    {
                        "test_path": f"/workspace/tests/{PROBE_FILE}",
                        "test_name": name,
                        "framework": "fastapi",
                        "method": "GET",
                        "route": None,
                        "path": "/",
                        "status": status,
                        "exception": exception,
                    }
                    for name, status, exception in calls
                ]
                + [
                    {  # a project test with the same name is not the probe
                        "test_path": "/workspace/src/tests/test_x.py",
                        "test_name": "test_probe_001_unknown_path",
                        "framework": "fastapi",
                        "method": "GET",
                        "route": None,
                        "path": "/",
                        "status": 500,
                    }
                ],
                "apps": [],
            }
        )
    )


def case_report(name: str, outcome: Outcome, message: str | None = None) -> TestCaseReport:
    return TestCaseReport(
        nodeid=f"tests/{PROBE_FILE}::{name}", file=f"tests/{PROBE_FILE}", line=1, name=name,
        outcome=outcome, duration_seconds=0.0, message=message, traceback=None, generated=True,
    )  # fmt: skip


def test_verdicts() -> None:
    plan = plan_probes(SHOP)
    names = [p.test for p in plan.probes]
    capture = recorded(
        (names[0], 404, False),  # unknown path: passed
        (names[1], 200, False),  # wrong method answered 200: unexpected
        (names[2], 500, True),  # an exception: server error
        (names[3], 503, False),  # a 5xx: server error
        (names[5], 200, False),  # empty body accepted: missing validation
        (names[6], 400, False),  # 4xx: passed
        (names[7], 302, False),  # invalid parameter redirected: accepted
        (names[8], 405, False),  # another 4xx: passed for "4xx"
        (names[9], None, False),  # no status recorded: unexpected
        (names[10], 404, False),
    )
    tests = [
        case_report(names[2], Outcome.FAILED, "KeyError: 'title'"),
        case_report(names[3], Outcome.PASSED),
        case_report(names[4], Outcome.ERROR, "ImportError: no module shop"),
    ]
    results = evaluate_probes(plan, capture, tests)
    assert [(r.verdict, r.status, r.detail) for r in results] == [
        (ProbeVerdict.PASSED, 404, None),
        (ProbeVerdict.UNEXPECTED, 200, None),
        (ProbeVerdict.SERVER_ERROR, 500, "KeyError: 'title'"),
        (ProbeVerdict.SERVER_ERROR, 503, None),
        (ProbeVerdict.NOT_RUN, None, "ImportError: no module shop"),
        (ProbeVerdict.ACCEPTED_INVALID, 200, None),
        (ProbeVerdict.PASSED, 400, None),
        (ProbeVerdict.ACCEPTED_INVALID, 302, None),
        (ProbeVerdict.PASSED, 405, None),
        (ProbeVerdict.UNEXPECTED, None, None),
        (ProbeVerdict.PASSED, 404, None),
        (ProbeVerdict.NOT_RUN, None, "the probe did not run"),
    ]
    assert results[0].kind is ProbeKind.UNKNOWN_PATH


@pytest.mark.parametrize("capture", [None])
def test_no_capture(capture: RawApiCapture | None) -> None:
    plan = plan_probes(SHOP)
    assert {r.verdict for r in evaluate_probes(plan, capture, [])} == {ProbeVerdict.NOT_RUN}
