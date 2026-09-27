"""API coverage: which endpoints the test suite actually calls, and how.

Inputs: the endpoints of the static discovery and what the `testmind_api` plugin recorded in
the sandbox (requests per test, and the route table of every application that answered).
The two endpoint lists are merged and cross-checked; every recorded request is attributed to
the endpoint whose route answered it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from testmind.analyzers.code.models import CodeProfile
from testmind.api_engine.capture import RawApiCapture, RawCall
from testmind.api_engine.models import (
    ANY_METHOD,
    ApiCoverageReport,
    ApiGap,
    ApiSurface,
    ApiTotals,
    Endpoint,
    EndpointCoverage,
    Framework,
    Handler,
    ProbeResult,
    Source,
    UnmatchedRequest,
)
from testmind.api_engine.paths import canonical, matches
from testmind.sandbox.paths import report_path

SUCCESS, CLIENT_ERROR, SERVER_ERROR = 200, 400, 500
MAX_LISTED = 5
GAP_ORDER = {"server_error": 0, "untested": 1, "error_only": 2, "success_only": 3}
FRAMEWORKS = {f.value: f for f in Framework}


class Routed(Protocol):
    """Anything with a framework, a method and a canonical path (an endpoint)."""

    @property
    def framework(self) -> Framework: ...
    @property
    def method(self) -> str: ...
    @property
    def path(self) -> str: ...


RoutedT = TypeVar("RoutedT", bound=Routed)


@dataclass(slots=True)
class _Merged:
    framework: Framework
    method: str
    path: str
    in_static: bool
    in_runtime: bool
    handler: Handler | None
    has_input: bool  # path parameters or a request body: there is something to validate


@dataclass(slots=True)
class _Tally:
    calls: int = 0
    tests: set[str] = field(default_factory=set)
    statuses: Counter[str] = field(default_factory=Counter)
    generated: int = 0
    successes: int = 0
    client_errors: int = 0
    server_errors: int = 0


def build_api_coverage(
    surface: ApiSurface,
    capture: RawApiCapture | None,
    *,
    code: CodeProfile | None = None,
    probes: list[ProbeResult] | None = None,
    notes: list[str] | None = None,
) -> ApiCoverageReport:
    notes = list(notes or [])
    runtime = _runtime_routes(capture, notes) if capture is not None else {}
    endpoints = _merge(surface.endpoints, runtime, notes, recorded=capture is not None)
    tallies: dict[tuple[Framework, str, str], _Tally] = defaultdict(_Tally)
    unmatched: dict[tuple[Framework, str, str, int | None], set[str]] = defaultdict(set)

    calls, rootdir = (capture.calls, capture.rootdir) if capture is not None else ([], "")
    for call in calls:
        framework = FRAMEWORKS.get(call.framework)
        if framework is None:
            continue
        test, generated = _test_id(call, rootdir)
        endpoint = attribute_call(call, endpoints)
        if endpoint is None:
            if not generated:  # probes ask for unknown paths on purpose
                key = (framework, call.method, _request_path(call, framework), call.status)
                unmatched[key].add(test)
            continue
        tally = tallies[(endpoint.framework, endpoint.method, endpoint.path)]
        status = call.status if not call.exception else SERVER_ERROR
        if status is not None and status >= SERVER_ERROR:
            tally.server_errors += call.count
        if generated:
            tally.generated += call.count
            continue
        tally.calls += call.count
        tally.tests.add(test)
        tally.statuses[str(status)] += call.count
        if status is not None and SUCCESS <= status < CLIENT_ERROR:
            tally.successes += call.count
        elif status is not None and CLIENT_ERROR <= status < SERVER_ERROR:
            tally.client_errors += call.count

    covered = [_coverage(e, tallies[(e.framework, e.method, e.path)]) for e in endpoints]
    tested = sum(1 for c in covered if c.tested)
    if capture is not None:
        if capture.dropped:
            notes.append(f"{capture.dropped} request record(s) beyond the capture limit")
        notes += [f"API capture: {error}" for error in capture.errors]
    frameworks = sorted({e.framework for e in endpoints} | {k[0] for k in unmatched})
    return ApiCoverageReport(
        frameworks=frameworks,
        totals=ApiTotals(
            endpoints=len(covered),
            tested=tested,
            success_tested=sum(1 for c in covered if c.success_tested),
            error_tested=sum(1 for c in covered if c.error_tested),
            percent=round(100 * tested / len(covered), 2) if covered else None,
        ),
        endpoints=covered,
        unmatched=[
            UnmatchedRequest(
                framework=framework, method=method, path=path, status=status, tests=sorted(tests)
            )
            for (framework, method, path, status), tests in sorted(
                unmatched.items(), key=lambda item: (item[0][2], item[0][1], item[0][3] or 0)
            )
        ],
        gaps=_gaps(endpoints, covered, code),
        probes=probes or [],
        notes=notes,
    )


# ------------------------------------------------------------------------------ endpoints
def _runtime_routes(
    capture: RawApiCapture, notes: list[str]
) -> dict[tuple[Framework, str], set[str]]:
    """(framework, canonical path) → methods registered by the running applications."""
    routes: dict[tuple[Framework, str], set[str]] = defaultdict(set)
    for app in capture.apps:
        framework = FRAMEWORKS.get(app.framework)
        if framework is None:
            notes.append(f"API capture: unknown framework {app.framework!r}")
            continue
        for route in app.routes:
            if route.internal:  # FastAPI's /docs, Flask's static files, Django's admin
                continue
            path, _ = canonical(framework, route.path)
            routes[(framework, path)].update(route.methods or [ANY_METHOD])
    return routes


def _merge(
    static: list[Endpoint],
    runtime: dict[tuple[Framework, str], set[str]],
    notes: list[str],
    *,
    recorded: bool,
) -> list[_Merged]:
    by_path: dict[tuple[Framework, str], list[Endpoint]] = defaultdict(list)
    for endpoint in static:
        by_path[(endpoint.framework, endpoint.path)].append(endpoint)
    runtime_frameworks = {framework for framework, _ in runtime}
    merged: list[_Merged] = []
    static_only: list[str] = []
    runtime_only: list[str] = []
    for key in sorted(set(by_path) | set(runtime)):
        framework, path = key
        endpoints = {e.method: e for e in by_path.get(key, [])}
        registered = runtime.get(key, set())
        static_methods = set(endpoints)
        specific = (static_methods | registered) - {ANY_METHOD}
        for method in sorted(specific or {ANY_METHOD}):
            in_static = method in static_methods or ANY_METHOD in static_methods
            in_runtime = method in registered or ANY_METHOD in registered
            source = endpoints.get(method) or endpoints.get(ANY_METHOD)
            if in_static and not in_runtime and framework in runtime_frameworks:
                static_only.append(f"{method} {path}")
            if in_runtime and not in_static:
                runtime_only.append(f"{method} {path}")
            merged.append(
                _Merged(
                    framework=framework,
                    method=method,
                    path=path,
                    in_static=in_static,
                    in_runtime=in_runtime,
                    handler=source.handler if source is not None else None,
                    has_input=(source is not None and (bool(source.params) or source.accepts_body))
                    or "{" in path,
                )
            )
    if static and not recorded:
        notes.append("No route table was recorded: the endpoints come from static discovery only")
    elif static and recorded and not runtime:
        notes.append(
            "No test sent a request to the application, so its route table was not recorded: "
            "the endpoints come from static discovery only"
        )
    if static_only:
        notes.append(
            f"{len(static_only)} endpoint(s) found statically are not registered by the running "
            f"application: {_listing(static_only)}"
        )
    if runtime_only:
        notes.append(
            f"{len(runtime_only)} endpoint(s) registered at run time were not found statically: "
            f"{_listing(runtime_only)}"
        )
    return merged


def _listing(items: list[str]) -> str:
    shown = ", ".join(items[:MAX_LISTED])
    return shown + (f" (+{len(items) - MAX_LISTED} more)" if len(items) > MAX_LISTED else "")


# ---------------------------------------------------------------------------------- calls
def _test_id(call: RawCall, rootdir: str) -> tuple[str, bool]:
    """(nodeid as TestMind reports tests, generated?)."""
    name = call.test_name or "<outside a test>"
    located = report_path(call.test_path, rootdir) if call.test_path else None
    if located is None:
        return name, False
    file, generated = located
    return f"{file}::{name}", generated


def attribute_call(call: RawCall, endpoints: Sequence[RoutedT]) -> RoutedT | None:
    """The endpoint whose route answered a recorded request, if any."""
    framework = FRAMEWORKS.get(call.framework)
    if framework is None:
        return None
    method = call.method.upper()
    candidates = [e for e in endpoints if e.framework is framework]
    if call.route is not None:
        path, _ = canonical(framework, call.route)
        same = [e for e in candidates if e.path == path]
    else:  # the framework named no route (e.g. Flask before routing): match the concrete path
        same = [e for e in candidates if matches(e.path, call.path)]
    return next((e for e in same if e.method in {method, ANY_METHOD}), None)


def _request_path(call: RawCall, framework: Framework) -> str:
    return canonical(framework, call.route)[0] if call.route is not None else call.path


# --------------------------------------------------------------------------------- report
def _coverage(endpoint: _Merged, tally: _Tally) -> EndpointCoverage:
    source = (
        Source.BOTH
        if endpoint.in_static and endpoint.in_runtime
        else Source.STATIC
        if endpoint.in_static
        else Source.RUNTIME
    )
    return EndpointCoverage(
        framework=endpoint.framework,
        method=endpoint.method,
        path=endpoint.path,
        source=source,
        handler=endpoint.handler,
        calls=tally.calls,
        tests=sorted(tally.tests),
        statuses=dict(sorted(tally.statuses.items())),
        generated_calls=tally.generated,
        tested=tally.calls > 0,
        success_tested=tally.successes > 0,
        error_tested=tally.client_errors > 0,
        server_errors=tally.server_errors,
    )


def _gaps(
    endpoints: list[_Merged], covered: list[EndpointCoverage], code: CodeProfile | None
) -> list[ApiGap]:
    complexity = (
        {(f.path, f.qualname): f.complexity for f in code.functions} if code is not None else {}
    )
    gaps: list[ApiGap] = []
    for endpoint, coverage in zip(endpoints, covered, strict=True):
        if coverage.server_errors:
            kind = "server_error"
            reason = f"{coverage.server_errors} request(s) answered with a server error (5xx)"
        elif not coverage.tested:
            kind, reason = "untested", "no test of the project sends a request to it"
        elif not coverage.success_tested:
            kind = "error_only"
            reason = "tested only with requests that fail (4xx): the successful path is not"
        elif endpoint.has_input and not coverage.error_tested:
            kind = "success_only"
            reason = (
                "only successful requests are tested: no test checks invalid input or a "
                "missing resource (4xx)"
            )
        else:
            continue
        handler = endpoint.handler
        gaps.append(
            ApiGap(
                method=endpoint.method,
                path=endpoint.path,
                kind=kind,
                reason=reason,
                handler=handler,
                complexity=complexity.get((handler.path, handler.qualname))
                if handler is not None and handler.path is not None
                else None,
            )
        )
    return sorted(gaps, key=lambda g: (GAP_ORDER[g.kind], -(g.complexity or 0), g.path, g.method))
