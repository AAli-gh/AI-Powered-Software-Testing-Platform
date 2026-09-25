from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    code: str = Field(examples=["not_found"])
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    """Every non-2xx response from the API has this shape."""

    error: ErrorBody


ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):  # PEP 695 generics would require Python 3.12
    items: list[ItemT]
    total: int
    limit: int
    offset: int
