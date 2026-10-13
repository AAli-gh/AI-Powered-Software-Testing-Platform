"""Request and response shapes for website scans."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from testmind.domain.enums import RunStatus
from testmind.web_engine.models import SiteScan


class WebScanCreate(BaseModel):
    """Start a scan of a public website."""

    model_config = ConfigDict(extra="forbid")

    target_url: Annotated[
        str, Field(min_length=1, max_length=2048, examples=["https://example.com/"])
    ]
    max_pages: Annotated[int, Field(ge=1, le=500)] = 100
    max_depth: Annotated[int, Field(ge=0, le=10)] = 5
    interact: Annotated[
        bool, Field(description="Click safe buttons and submit forms to find dead controls")
    ] = False
    security: Annotated[
        bool, Field(description="Passive security checks (headers, cookies, mixed content)")
    ] = False


class WebScanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_url: str
    status: RunStatus
    max_pages: int
    max_depth: int
    interact: bool
    security: bool
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    result: SiteScan | None
