"""Request and response shapes for authorized targets (ownership-verified hosts)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, computed_field

from testmind.web_engine.ownership import WELL_KNOWN_PATH


class AuthorizedTargetCreate(BaseModel):
    """Register a host you own. A verification token is issued in return."""

    model_config = ConfigDict(extra="forbid")

    host: Annotated[str, Field(min_length=1, max_length=255, examples=["example.com"])]


class AuthorizedTargetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    host: str
    token: str
    verified: bool
    verified_at: datetime | None
    last_error: str | None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def instructions(self) -> str:
        return (
            f"Publish a file at https://{self.host}{WELL_KNOWN_PATH} whose contents include the "
            f"token above, then verify. (http is accepted as a fallback.)"
        )
