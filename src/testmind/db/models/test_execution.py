from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Text, false
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
    # Serialised CoverageReport, and its total percentage (cheap to list and compare).
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    coverage_percent: Mapped[float | None] = mapped_column(Float)
    # Serialised FailureReport: the deterministic failure analysis, made with every execution.
    failures: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Run TestMind's deterministic API probes with the suite (api_engine/probes.py).
    api_probes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    # Serialised ApiCoverageReport, and its share of endpoints the project's tests call.
    api_coverage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    api_coverage_percent: Mapped[float | None] = mapped_column(Float)
    # Which tests executed which production function (fault_localization.Spectrum, compact
    # JSON): ranks the suspects of any set of failing tests after the run.
    spectrum: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
