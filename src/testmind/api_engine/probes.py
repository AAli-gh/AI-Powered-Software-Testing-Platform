"""Deterministic API probes: requests whose correct answer the framework semantics fix.

For every FastAPI or Flask application a test can import (`module:app` or a factory without
required arguments), TestMind writes one pytest module, from a template and never with an
LLM:
- an unknown path must be answered 404;
- a method that no route of a path accepts must be answered 405;
- a non-numeric value for a numeric path parameter must be rejected (4xx);
- for endpoints that read a body: `{}` and malformed JSON must be rejected (4xx).

The module runs in the sandbox with the suite. Each probe's verdict comes from what the
`testmind_api` plugin recorded for it: a 5xx or an exception is a server error (a bug), a
2xx/3xx for invalid input means the input is not validated. Django is not probed: its views
need the project's database set up by its own test runner.
"""

from __future__ import annotations

from dataclasses import dataclass

from testmind.api_engine.capture import RawApiCapture
from testmind.api_engine.models import (
    ANY_METHOD,
    ApiApp,
    ApiSurface,
    Endpoint,
    Framework,
    ProbeKind,
    ProbeResult,
    ProbeVerdict,
)
from testmind.api_engine.paths import matches
from testmind.sandbox.models import Outcome
from testmind.sandbox.paths import report_path
from testmind.test_engine.models import TestCaseReport

PROBE_FILE = "testmind/test_api_probes.py"  # relative to the generated tests directory
PROBED = frozenset({Framework.FASTAPI, Framework.FLASK})
UNKNOWN_PATH = "/__testmind_probe__/no-such-path"
METHOD_CANDIDATES = ("DELETE", "PUT", "PATCH", "POST", "GET")
BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
NUMERIC = frozenset({"int", "float"})
SAMPLE = {"int": "1", "float": "1.5", "uuid": "00000000-0000-4000-8000-000000000001"}
INVALID_VALUE = "not-a-number"
MALFORMED_JSON = '{"unterminated'
MAX_PROBES = 200
CLIENT_ERROR, SERVER_ERROR = 400, 500


@dataclass(frozen=True, slots=True)
class Probe:
    test: str
    kind: ProbeKind
    client: int  # index of the application in the module
    method: str  # the endpoint's method (or the path's, for wrong_method)
    path: str  # the endpoint's template
    request_method: str
    request_path: str
    body: str | None  # None, "{}" or malformed JSON (sent as application/json)
    expected: str  # "404", "405" or "4xx"

    @property
    def request(self) -> str:
        suffix = f" with body {self.body}" if self.body is not None else ""
        return f"{self.request_method} {self.request_path}{suffix}"


@dataclass(frozen=True, slots=True)
class ProbePlan:
    probes: list[Probe]
    module: str | None  # the pytest module, None when there is nothing to probe
    notes: list[str]


def plan_probes(surface: ApiSurface, *, limit: int = MAX_PROBES) -> ProbePlan:
    notes: list[str] = []
    apps: list[ApiApp] = []
    for app in surface.apps:
        if app.framework not in PROBED:
            notes.append(f"{app.framework.value} application {app.module} is not probed")
        elif app.target is None:
            notes.append(
                f"{app.framework.value} application in {app.path}:{app.line} is not probed: "
                "it is created by a factory that needs arguments"
            )
        else:
            apps.append(app)
    probes: list[Probe] = []
    for index, app in enumerate(apps):
        endpoints = [e for e in surface.endpoints if e.app == app.target]
        probes += _probes_for(index, endpoints, len(probes))
    if len(probes) > limit:
        notes.append(f"{len(probes)} probes planned; the first {limit} were run")
        probes = probes[:limit]
    if not probes:
        return ProbePlan([], None, notes)
    return ProbePlan(probes, _module(apps, probes), notes)


def _probes_for(client: int, endpoints: list[Endpoint], start: int) -> list[Probe]:
    probes: list[Probe] = []

    def add(
        kind: ProbeKind,
        target: tuple[str, str],
        request: tuple[str, str],
        expected: str,
        *,
        body: str | None = None,
    ) -> None:
        """`target`: (endpoint method, template); `request`: (method, concrete path)."""
        probes.append(
            Probe(
                test=f"test_probe_{start + len(probes) + 1:03d}_{kind.value}",
                kind=kind,
                client=client,
                method=target[0],
                path=target[1],
                request_method=request[0],
                request_path=request[1],
                body=body,
                expected=expected,
            )
        )

    if not any(matches(e.path, UNKNOWN_PATH) for e in endpoints):
        add(ProbeKind.UNKNOWN_PATH, ("GET", UNKNOWN_PATH), ("GET", UNKNOWN_PATH), "404")

    by_path: dict[str, list[Endpoint]] = {}
    for endpoint in endpoints:
        by_path.setdefault(endpoint.path, []).append(endpoint)
    for path, group in sorted(by_path.items()):
        allowed = {e.method for e in group}
        concrete = _concrete(group[0])
        method = next((m for m in METHOD_CANDIDATES if m not in allowed), None)
        if (
            ANY_METHOD not in allowed
            and method is not None
            and not _answered(endpoints, method, concrete)
        ):
            target = ("/".join(sorted(allowed)), path)
            add(ProbeKind.WRONG_METHOD, target, (method, concrete), "405")

    for endpoint in sorted(endpoints, key=lambda e: (e.path, e.method)):
        target = (endpoint.method, endpoint.path)
        numeric = next((p for p in endpoint.params if p.type in NUMERIC), None)
        if numeric is not None:
            invalid = _concrete(endpoint, override={numeric.name: INVALID_VALUE})
            others = [e for e in endpoints if e is not endpoint]
            if not _answered(others, endpoint.method, invalid):
                add(ProbeKind.INVALID_PARAM, target, (endpoint.method, invalid), "4xx")
        if endpoint.accepts_body and endpoint.method in BODY_METHODS:
            request = (endpoint.method, _concrete(endpoint))
            add(ProbeKind.EMPTY_BODY, target, request, "4xx", body="{}")
            add(ProbeKind.MALFORMED_BODY, target, request, "4xx", body=MALFORMED_JSON)
    return probes


def _concrete(endpoint: Endpoint, override: dict[str, str] | None = None) -> str:
    """The template with a valid sample value for every parameter (or the overrides)."""
    path = endpoint.path
    for param in endpoint.params:
        value = (override or {}).get(param.name, SAMPLE.get(param.type, "probe"))
        path = path.replace("{" + param.name + "}", value)
    return path


def _answered(endpoints: list[Endpoint], method: str, path: str) -> bool:
    """Would another route answer this request (so the probe would not test what it says)?"""
    return any(e.method in {method, ANY_METHOD} and matches(e.path, path) for e in endpoints)


# ------------------------------------------------------------------------------ the module
def _module(apps: list[ApiApp], probes: list[Probe]) -> str:
    imports: dict[str, None] = {}  # ordered and without duplicates
    setup: list[str] = []
    for index, app in enumerate(apps):
        module, _, attribute = (app.target or "").partition(":")
        imports[f"from {module} import {attribute.removesuffix('()')} as _source_{index}"] = None
        if app.framework is Framework.FASTAPI:
            imports["from fastapi.testclient import TestClient"] = None
            setup.append(f"client_{index} = TestClient(_source_{index})")
        else:
            call = "()" if attribute.endswith("()") else ""
            setup += [
                f"_app_{index} = _source_{index}{call}",
                f"_app_{index}.testing = True  # exceptions reach the test with their traceback",
                f"client_{index} = _app_{index}.test_client()",
            ]
    lines = [
        '"""Deterministic API probes written by TestMind from a template (no LLM)."""',
        "",
        *sorted(imports),
        "",
        *setup,
    ]
    for probe in probes:
        lines += ["", "", *_probe_function(probe, apps[probe.client].framework)]
    return "\n".join(lines) + "\n"


def _probe_function(probe: Probe, framework: Framework) -> list[str]:
    if framework is Framework.FASTAPI:  # httpx: request(method, url, content=…)
        arguments = [repr(probe.request_method), repr(probe.request_path)]
        body_argument = "content"
        call = "request"
    else:  # werkzeug: open(path, method=…, data=…)
        arguments = [repr(probe.request_path), f"method={probe.request_method!r}"]
        body_argument = "data"
        call = "open"
    if probe.body is not None:
        arguments += [
            f"{body_argument}={probe.body!r}",
            "headers={'Content-Type': 'application/json'}",
        ]
    check = (
        f"response.status_code == {probe.expected}"
        if probe.expected.isdigit()
        else f"{CLIENT_ERROR} <= response.status_code < {SERVER_ERROR}"
    )
    return [
        f"def {probe.test}():",
        f'    """{probe.kind.value}: {probe.request} must be answered {probe.expected}."""',
        f"    response = client_{probe.client}.{call}({', '.join(arguments)})",
        f"    assert {check}, response.status_code",
    ]


# -------------------------------------------------------------------------------- verdicts
def evaluate_probes(
    plan: ProbePlan, capture: RawApiCapture | None, tests: list[TestCaseReport]
) -> list[ProbeResult]:
    """Each probe's verdict from its recorded request (and its test when none was recorded)."""
    recorded: dict[str, tuple[int | None, bool]] = {}
    if capture is not None:
        for call in capture.calls:
            located = report_path(call.test_path, capture.rootdir) if call.test_path else None
            if located is not None and located[1] and located[0].endswith(PROBE_FILE):
                recorded.setdefault(call.test_name or "", (call.status, call.exception))
    outcomes = {t.name: t for t in tests if t.generated and (t.file or "").endswith(PROBE_FILE)}
    return [_verdict(p, recorded.get(p.test), outcomes.get(p.test)) for p in plan.probes]


def _verdict(
    probe: Probe, recorded: tuple[int | None, bool] | None, test: TestCaseReport | None
) -> ProbeResult:
    status: int | None = None
    detail: str | None = None
    if recorded is None:
        verdict = ProbeVerdict.NOT_RUN
        detail = test.message if test is not None and test.message else "the probe did not run"
    else:
        status, exception = recorded
        if exception or (status is not None and status >= SERVER_ERROR):
            verdict = ProbeVerdict.SERVER_ERROR
            detail = (
                test.message if test is not None and test.outcome is not Outcome.PASSED else None
            )
        elif status is not None and _expected(probe.expected, status):
            verdict = ProbeVerdict.PASSED
        elif status is not None and status < CLIENT_ERROR and probe.kind in {
            ProbeKind.INVALID_PARAM, ProbeKind.EMPTY_BODY, ProbeKind.MALFORMED_BODY,
        }:  # fmt: skip
            verdict = ProbeVerdict.ACCEPTED_INVALID
        else:
            verdict = ProbeVerdict.UNEXPECTED
    return ProbeResult(
        test=probe.test,
        kind=probe.kind,
        method=probe.method,
        path=probe.path,
        request=probe.request,
        expected=probe.expected,
        status=status,
        verdict=verdict,
        detail=detail,
    )


def _expected(expected: str, status: int) -> bool:
    if expected.isdigit():
        return status == int(expected)
    return CLIENT_ERROR <= status < SERVER_ERROR
