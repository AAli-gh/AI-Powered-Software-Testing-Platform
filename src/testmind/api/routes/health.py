from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from testmind import __version__
from testmind.core.logging import get_logger

router = APIRouter(tags=["health"])
logger = get_logger(__name__)

CHECK_TIMEOUT_SECONDS = 2.0

CheckResult = Literal["ok", "unavailable"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, CheckResult]


@router.get("/health", summary="Liveness probe")
async def health() -> HealthResponse:
    """The process is up and serving requests. Does not touch dependencies."""
    return HealthResponse(status="ok", version=__version__)


@router.get(
    "/ready",
    summary="Readiness probe",
    responses={503: {"model": ReadinessResponse, "description": "A dependency is unavailable"}},
)
async def ready(request: Request, response: Response) -> ReadinessResponse:
    """Verifies real connectivity to PostgreSQL and Redis."""
    database, redis = await asyncio.gather(_check_database(request), _check_redis(request))
    checks: dict[str, CheckResult] = {"database": database, "redis": redis}
    if all(result == "ok" for result in checks.values()):
        return ReadinessResponse(status="ready", checks=checks)
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(status="not_ready", checks=checks)


async def _check_database(request: Request) -> CheckResult:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with request.app.state.session_factory() as session:
                await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("readiness_check_failed", dependency="database", error=repr(exc))
        return "unavailable"
    return "ok"


async def _check_redis(request: Request) -> CheckResult:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await request.app.state.redis.ping()
    except Exception as exc:
        logger.warning("readiness_check_failed", dependency="redis", error=repr(exc))
        return "unavailable"
    return "ok"
