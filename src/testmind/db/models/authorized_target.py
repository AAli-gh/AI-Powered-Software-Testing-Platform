from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class AuthorizedTarget(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A host the user has proven they own, so active (intrusive) scans may run against it.

    `token` is published by the owner at a well-known path; once `verified` is true the host is
    cleared for active checks. Verification can be rechecked, so `verified` may go back to false.
    """

    __tablename__ = "authorized_targets"

    host: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    token: Mapped[str] = mapped_column(String(128), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
