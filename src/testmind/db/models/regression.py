from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus
from testmind.regression.models import RegressionMode, Verdict

if TYPE_CHECKING:
    from testmind.db.models.project import Project


class Regression(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A regression analysis between two commits of a project's git repository."""

    __tablename__ = "regressions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "regression_status"), nullable=False, default=RunStatus.QUEUED
    )
    mode: Mapped[RegressionMode] = mapped_column(
        str_enum(RegressionMode, "regression_mode"), nullable=False
    )
    base_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    head_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    # Resolved when the job was requested, so later branch moves do not change the job.
    base_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    head_commit: Mapped[str] = mapped_column(String(40), nullable=False)
    verdict: Mapped[Verdict | None] = mapped_column(str_enum(Verdict, "regression_verdict"))
    # Serialised RegressionReport.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship()
