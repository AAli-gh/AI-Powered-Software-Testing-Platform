from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus


class WebScan(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A crawl of a website: visit its pages, exercise its controls, and record the issues found.

    The crawl runs in the background. `result` holds the serialised `SiteScan` once it finishes.
    """

    __tablename__ = "web_scans"

    target_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "web_scan_status"), nullable=False, default=RunStatus.QUEUED
    )
    # Crawl budget and behaviour.
    max_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    interact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Passive security checks (response headers, cookies, mixed content); no attack is performed.
    security: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Whether internal/loopback hosts are in scope. False for public scans; the authorized-target
    # profile (ownership verified) is what may set it true later.
    allow_internal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Serialised SiteScan.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
