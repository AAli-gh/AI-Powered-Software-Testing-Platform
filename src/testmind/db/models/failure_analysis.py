from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus


class FailureAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One requested AI analysis of a test execution's failure groups.

    The deterministic analysis is not a job: it is stored with the execution itself
    (`test_executions.failures`).
    """

    __tablename__ = "failure_analyses"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("test_executions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "failure_analysis_status"), nullable=False, default=RunStatus.QUEUED
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    # Serialised FailureAnalysisResult.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
