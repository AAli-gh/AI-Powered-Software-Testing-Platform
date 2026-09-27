from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from testmind.domain.enums import SourceType


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$",
        examples=["discount-service"],
    )
    source_type: Literal[SourceType.LOCAL] = Field(
        default=SourceType.LOCAL,
        description="Only local directories; GitHub projects are created by importing "
        "(POST /api/v1/github/imports)",
    )
    source_location: str = Field(
        min_length=1,
        max_length=1024,
        description="For local sources: a directory inside TESTMIND_REPOS_ROOT "
        "(relative to it, or absolute).",
        examples=["discount_service"],
    )


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    source_type: SourceType
    source_location: str
    remote_repository: str | None = Field(description="GitHub imports: owner/name")
    remote_ref: str | None
    remote_commit: str | None
    created_at: datetime
    updated_at: datetime
