from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus
from testmind.test_engine.models import ExecutionOutcome


class TestExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One sandboxed run of a repository's test suite (optionally with generated tests)."""

    __tablename__ = "test_executions"
    __test__ = False

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    generation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_generations.id", ondelete="SET NULL")
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "execution_status"), nullable=False, default=RunStatus.QUEUED
    )
    # Test-level verdict (passed / failed / ...), separate from the job lifecycle above.
    outcome: Mapped[ExecutionOutcome | None] = mapped_column(
        str_enum(ExecutionOutcome, "execution_outcome")
    )
    # Serialised TestRunReport.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
