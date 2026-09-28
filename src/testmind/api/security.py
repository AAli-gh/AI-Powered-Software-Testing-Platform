"""API authentication and rate limiting (pure ASGI middleware).

Authentication: with TESTMIND_API_TOKEN set, every /api/ request needs
`Authorization: Bearer <token>`, compared in constant time, and so does /metrics (Prometheus
sends it as its bearer credentials). The GitHub webhook is the one exception: it authenticates
with its own HMAC signature. Liveness, readiness and the API reference stay open. Production
refuses to start without a token (core/config.py).

Rate limiting: requests that change state (POST, PUT, PATCH, DELETE under /api/) are counted
per client and minute in Redis (fixed window, shared by every API process). Beyond the limit
the answer is 429 with Retry-After. If Redis cannot be reached the request is let through:
limiting protects capacity, it must not take the API down (readiness reports Redis).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from pydantic import SecretStr
from redis.exceptions import RedisError
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from testmind.api.errors import error_response
from testmind.core.logging import get_logger

logger = get_logger(__name__)

WEBHOOK = "/api/v1/github/webhook"
METRICS = "/metrics"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
WINDOW_SECONDS = 60


def _protected(scope: Scope) -> bool:
    path: str = scope["path"]
    return (path.startswith("/api/") and path != WEBHOOK) or path == METRICS


class TokenAuthMiddleware:
    def __init__(self, app: ASGIApp, token: SecretStr | None) -> None:
        self.app = app
        self._token = token.get_secret_value().encode() if token is not None else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._token is None or scope["type"] != "http" or not _protected(scope):
            await self.app(scope, receive, send)
            return
        scheme, _, presented = Headers(scope=scope).get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode(), self._token
        ):
            response = error_response(
                401, "unauthorized", "A valid API token is required (Authorization: Bearer …)",
                headers={"WWW-Authenticate": 'Bearer realm="testmind"'},
            )  # fmt: skip
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, per_minute: int) -> None:
        self.app = app
        self._limit = per_minute

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            self._limit <= 0
            or scope["type"] != "http"
            or scope["method"] not in MUTATING
            or not _protected(scope)
        ):
            await self.app(scope, receive, send)
            return
        now = time.time()
        window = int(now // WINDOW_SECONDS)
        key = f"testmind:ratelimit:{client_key(scope)}:{window}"
        try:
            count = await _count(scope["app"].state.redis, key)
        except (RedisError, OSError, AttributeError):
            logger.warning("rate_limit_unavailable")
            await self.app(scope, receive, send)
            return
        if count > self._limit:
            retry_after = max(1, int((window + 1) * WINDOW_SECONDS - now))
            response = error_response(
                429, "rate_limited",
                f"More than {self._limit} changes per minute; retry in {retry_after} s",
                headers={"Retry-After": str(retry_after)},
            )  # fmt: skip
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _count(redis: Any, key: str) -> int:
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, WINDOW_SECONDS * 2)
        count, _ = await pipe.execute()
    return int(count)


def client_key(scope: Scope) -> str:
    """Who is counted: the client address (behind the dashboard's proxy, the proxy's)."""
    client = scope.get("client")
    host = client[0] if client else "unknown"
    return hashlib.sha256(host.encode()).hexdigest()[:16]
