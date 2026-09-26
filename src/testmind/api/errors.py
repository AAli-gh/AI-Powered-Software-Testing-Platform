"""Translate exceptions into the uniform `ErrorResponse` body."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from testmind.core.errors import (
    AppError,
    ConflictError,
    DispatchError,
    InvalidSourceError,
    LLMUnavailableError,
    NotFoundError,
)
from testmind.core.logging import get_logger
from testmind.schemas.common import ErrorBody, ErrorResponse

logger = get_logger(__name__)

STATUS_BY_ERROR: dict[type[AppError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidSourceError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    DispatchError: status.HTTP_503_SERVICE_UNAVAILABLE,
    LLMUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
}

HTTP_ERROR_CODES: dict[int, str] = {
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def _status_for(exc: AppError) -> int:
    for error_type in type(exc).__mro__:
        if error_type in STATUS_BY_ERROR:
            return STATUS_BY_ERROR[error_type]
    return status.HTTP_400_BAD_REQUEST


async def _app_error(_: Request, exc: AppError) -> JSONResponse:
    return error_response(_status_for(exc), exc.code, exc.message)


async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Only expose location/message/type — raw inputs may be large or sensitive.
    details = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", "Invalid request", details
    )


async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    headers = dict(exc.headers) if exc.headers else None
    return error_response(exc.status_code, code, str(exc.detail), headers=headers)


async def _unhandled_error(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error", exc_info=exc)
    return error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "Internal server error"
    )


# Handlers take the concrete exception type; `Any` lets them satisfy Starlette's signature.
Handler = Callable[[Request, Any], Awaitable[JSONResponse]]

HANDLERS: list[tuple[type[Exception], Handler]] = [
    (AppError, _app_error),
    (RequestValidationError, _validation_error),
    (StarletteHTTPException, _http_error),
    (Exception, _unhandled_error),
]


def install_exception_handlers(app: FastAPI) -> None:
    for exc_type, handler in HANDLERS:
        app.add_exception_handler(exc_type, handler)
