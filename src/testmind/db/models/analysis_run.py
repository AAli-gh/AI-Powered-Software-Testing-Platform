from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus

if TYPE_CHECKING:
    from testmind.db.models.project import Project


class AnalysisRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_runs"

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "run_status"), nullable=False, default=RunStatus.QUEUED, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="runs")
