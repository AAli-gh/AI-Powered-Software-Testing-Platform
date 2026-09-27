"""pytest plugin baked into the TestMind sandbox image (it is not part of the testmind package).

It records the HTTP requests that tests send to in-process applications through the
frameworks' test clients (FastAPI / Starlette, Flask, Django):
- which test sent it, the method, the route that answered (as the framework names it: the
  route template, not the concrete path) and the status code;
- the route table of every application that answered a request.

Nothing about the application changes: the frameworks' request entry points are wrapped, the
original is always called, and a failure of the instrumentation never fails a test (it is
reported in `errors`). The result is written to /out/api.json; TestMind reads it back as
untrusted input (docs/api-testing.md).
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib.util
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

OUTPUT = os.environ.get("TESTMIND_API_FILE", "/out/api.json")
MAX_RECORDS = 5000
MAX_APPS = 50
MAX_ROUTES = 2000
MAX_TEXT = 500
IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})

_current: dict[str, str | None] = {"path": None, "name": None}
_records: dict[tuple[Any, ...], dict[str, Any]] = {}
_apps: dict[tuple[Any, ...], dict[str, Any]] = {}
_seen_apps: set[int] = set()
_counts = {"dropped": 0}
_errors: list[str] = []
# Set while a Starlette application handles a request; the matched route fills it in.
_route: contextvars.ContextVar[dict[str, str | None] | None] = contextvars.ContextVar(
    "testmind_route", default=None
)


def _text(value: object) -> str:
    return str(value)[:MAX_TEXT]


def _record(
    framework: str,
    method: str,
    route: str | None,
    path: str,
    status: int | None,
    *,
    exception: bool = False,
) -> None:
    key = (
        _current["path"],
        _current["name"],
        framework,
        method,
        route,
        path if route is None else None,
        status,
        exception,
    )
    record = _records.get(key)
    if record is not None:
        record["count"] += 1
        return
    if len(_records) >= MAX_RECORDS:
        _counts["dropped"] += 1
        return
    _records[key] = {
        "test_path": _current["path"],
        "test_name": _current["name"],
        "framework": framework,
        "method": _text(method).upper(),
        "route": None if route is None else _text(route),
        "path": _text(path),
        "status": status,
        "exception": exception,
        "count": 1,
    }


def _handler_name(function: object) -> str | None:
    module = getattr(function, "__module__", None)
    qualname = getattr(function, "__qualname__", None)
    if not isinstance(module, str) or not isinstance(qualname, str):
        return None
    return f"{module}:{qualname}"


def _add_app(framework: str, app: object, collect: Callable[[], list[dict[str, Any]]]) -> None:
    if id(app) in _seen_apps:
        return
    _seen_apps.add(id(app))
    try:
        routes = collect()[:MAX_ROUTES]
    except Exception as exc:  # an unexpected framework version: report, never fail a test
        _errors.append(f"{framework} route table: {type(exc).__name__}: {_text(exc)}")
        return
    # Factories create a new application per test; identical route tables are kept once.
    key = (framework, json.dumps(routes, sort_keys=True))
    if key not in _apps and len(_apps) < MAX_APPS:
        _apps[key] = {"framework": framework, "routes": routes}


def _route_entry(
    path: str, methods: object, handler: object, *, internal: bool = False
) -> dict[str, Any]:
    listed = sorted({str(m).upper() for m in methods or ()} - IGNORED_METHODS) if methods else []
    name = _handler_name(handler)
    return {
        "path": _text(path),
        "methods": listed or (["ANY"] if methods is None else []),
        "handler": name,
        "internal": internal,
    }


# ------------------------------------------------------------------------ FastAPI / Starlette
def _starlette_entry(path: str, methods: object, handler: object) -> dict[str, Any]:
    internal = str(getattr(handler, "__module__", "")).startswith(("fastapi.", "starlette."))
    return _route_entry(path, methods or ["GET"], handler, internal=internal)


def _starlette_routes(routes: list[Any], prefix: str) -> list[dict[str, Any]]:
    from starlette.routing import Mount, Route  # noqa: PLC0415 - only when installed

    found = []
    for route in routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):  # FastAPI >= 0.141: an included router, resolved lazily
            for context in contexts():
                path = getattr(context, "path", None)
                if getattr(context, "methods", None) and isinstance(path, str):  # not WS
                    handler = getattr(context, "endpoint", None)
                    found.append(_starlette_entry(prefix + path, context.methods, handler))
        elif isinstance(route, Route):
            found.append(_starlette_entry(prefix + route.path, route.methods, route.endpoint))
        elif isinstance(route, Mount):
            children = list(getattr(route, "routes", []) or [])
            found += _starlette_routes(children, prefix + route.path)
    return found


def _mount_prefix(scope: dict[str, Any]) -> str:
    """The prefix of the Mount(s) the request went through."""
    root = str(scope.get("root_path", ""))
    app_root = str(scope.get("app_root_path", ""))
    return root[len(app_root) :] if root.startswith(app_root) else root


def _starlette_template(scope: dict[str, Any], holder: dict[str, str | None]) -> str | None:
    # FastAPI >= 0.141 matches included routes through an "effective route context" that
    # carries the full path; older versions (and Starlette) run Route.handle.
    fastapi_scope = scope.get("fastapi")
    context = (
        fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    )
    path = getattr(context, "path", None)
    if isinstance(path, str) and path:
        return _mount_prefix(scope) + path
    return holder["route"]


def _instrument_starlette() -> None:
    from starlette.applications import Starlette  # noqa: PLC0415 - only when installed
    from starlette.routing import Route  # noqa: PLC0415 - only when installed

    original_call = Starlette.__call__
    original_handle = Route.handle

    async def call(self: Any, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or _route.get() is not None:
            await original_call(self, scope, receive, send)  # not HTTP, or a mounted app
            return
        framework = "fastapi" if type(self).__module__.startswith("fastapi") else "starlette"
        _add_app(framework, self, lambda: _starlette_routes(list(self.routes), ""))
        holder: dict[str, str | None] = {"route": None}
        token = _route.set(holder)
        status: dict[str, int | None] = {"code": None}

        async def watch(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start" and status["code"] is None:
                status["code"] = message.get("status")
            await send(message)

        method, path = str(scope.get("method", "")), str(scope.get("path", ""))
        try:
            await original_call(self, scope, receive, watch)
        except BaseException:
            route = _starlette_template(scope, holder)
            _record(framework, method, route, path, status["code"] or 500, exception=True)
            raise
        finally:
            _route.reset(token)
        _record(framework, method, _starlette_template(scope, holder), path, status["code"])

    async def handle(self: Any, scope: dict[str, Any], receive: Any, send: Any) -> None:
        holder = _route.get()
        if holder is not None:
            holder["route"] = _mount_prefix(scope) + self.path
        await original_handle(self, scope, receive, send)

    Starlette.__call__ = call
    Route.handle = handle


# ------------------------------------------------------------------------------------ Flask
def _instrument_flask() -> None:
    from flask import Flask, request  # noqa: PLC0415 - only when installed

    original = Flask.full_dispatch_request

    def routes_of(app: Any) -> list[dict[str, Any]]:
        return [
            _route_entry(
                rule.rule,
                rule.methods,
                app.view_functions.get(rule.endpoint),
                internal=rule.endpoint == "static",
            )
            for rule in app.url_map.iter_rules()
        ]

    def full_dispatch_request(self: Any) -> Any:
        _add_app("flask", self, lambda: routes_of(self))
        method, path = request.method, request.path
        try:
            response = original(self)
        except BaseException:
            rule = request.url_rule
            _record(
                "flask", method, rule.rule if rule is not None else None, path, 500, exception=True
            )
            raise
        rule = request.url_rule
        _record(
            "flask", method, rule.rule if rule is not None else None, path, response.status_code
        )
        return response

    Flask.full_dispatch_request = full_dispatch_request


# ----------------------------------------------------------------------------------- Django
def _instrument_django() -> None:
    from django.core.handlers.base import BaseHandler  # noqa: PLC0415 - only when installed

    original = BaseHandler.get_response
    original_async = BaseHandler.get_response_async

    def routes_of() -> list[dict[str, Any]]:
        from django.urls import (  # noqa: PLC0415 - only when installed
            URLPattern,
            URLResolver,
            get_resolver,
        )

        def walk(patterns: list[Any], prefix: str) -> list[dict[str, Any]]:
            found = []
            for pattern in patterns:
                if isinstance(pattern, URLResolver):
                    found += walk(list(pattern.url_patterns), prefix + str(pattern.pattern))
                elif isinstance(pattern, URLPattern):
                    callback = pattern.callback
                    view_class = getattr(callback, "view_class", None)
                    methods = None
                    handler: object = callback
                    if view_class is not None:
                        handler = view_class
                        methods = [
                            m
                            for m in getattr(view_class, "http_method_names", [])
                            if m != "options" and hasattr(view_class, m)
                        ]
                    internal = getattr(handler, "__module__", "").startswith("django.")
                    found.append(
                        _route_entry(
                            prefix + str(pattern.pattern), methods, handler, internal=internal
                        )
                    )
            return found

        return walk(list(get_resolver().url_patterns), "")

    def finish(request: Any, response: Any, *, exception: bool) -> None:
        match = getattr(request, "resolver_match", None)
        route = getattr(match, "route", None) if match is not None else None
        status = 500 if exception else getattr(response, "status_code", None)
        _record("django", request.method or "", route, request.path, status, exception=exception)

    def get_response(self: Any, request: Any) -> Any:
        _add_app("django", self, routes_of)
        try:
            response = original(self, request)
        except BaseException:
            finish(request, None, exception=True)
            raise
        finish(request, response, exception=False)
        return response

    async def get_response_async(self: Any, request: Any) -> Any:
        _add_app("django", self, routes_of)
        try:
            response = await original_async(self, request)
        except BaseException:
            finish(request, None, exception=True)
            raise
        finish(request, response, exception=False)
        return response

    BaseHandler.get_response = get_response
    BaseHandler.get_response_async = get_response_async


INSTRUMENTS = {
    "starlette": _instrument_starlette,
    "flask": _instrument_flask,
    "django": _instrument_django,
}


def pytest_configure(config: pytest.Config) -> None:
    for module, instrument in INSTRUMENTS.items():
        if importlib.util.find_spec(module) is None:
            continue
        try:
            instrument()
        except Exception as exc:
            _errors.append(f"{module}: {type(exc).__name__}: {_text(exc)}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> Any:
    _current["path"], _current["name"] = str(item.path), item.name
    try:
        yield
    finally:
        _current["path"] = _current["name"] = None


def pytest_sessionfinish(session: pytest.Session) -> None:
    document = {
        "version": 1,
        "rootdir": str(session.config.rootpath),
        "calls": list(_records.values()),
        "apps": list(_apps.values()),
        "dropped": _counts["dropped"],
        "errors": _errors,
    }
    # /out is missing when pytest is not run by TestMind: then there is nothing to report.
    with contextlib.suppress(OSError):
        Path(OUTPUT).write_text(json.dumps(document), encoding="utf-8")
