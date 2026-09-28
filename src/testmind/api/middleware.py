"""Request correlation, access logging and HTTP metrics (pure ASGI — no BaseHTTPMiddleware
overhead)."""

from __future__ import annotations

import re
import time
import uuid

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from testmind.core.logging import get_logger
from testmind.core.metrics import HTTP_DURATION, HTTP_REQUESTS

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_QUIET_PATHS = frozenset({"/health", "/ready", "/metrics"})

logger = get_logger("testmind.access")


class RequestContextMiddleware:
    """Assigns a request id (or accepts a well-formed incoming one), binds it to every log line
    emitted while handling the request, echoes it in the response, logs one access line and
    records the request's metrics under its route template (`unmatched` for unknown paths)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        status_code = 500
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_request_id)
            finally:
                seconds = time.perf_counter() - started
                log = logger.debug if scope["path"] in _QUIET_PATHS else logger.info
                log(
                    "http_request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status_code,
                    duration_ms=round(seconds * 1000, 2),
                )
                route = route_label(scope)
                HTTP_REQUESTS.labels(scope["method"], route, str(status_code)).inc()
                HTTP_DURATION.labels(scope["method"], route).observe(seconds)


def route_label(scope: Scope) -> str:
    """The matched route's template (`/api/v1/runs/{run_id}`), never the raw path."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"
