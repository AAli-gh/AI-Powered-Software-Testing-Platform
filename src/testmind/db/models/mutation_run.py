from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus


class MutationRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Mutation testing of an analysed repository."""

    __tablename__ = "mutation_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "mutation_status"), nullable=False, default=RunStatus.QUEUED
    )
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Serialised MutationReport, and its score (cheap to list and compare).
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    score: Mapped[float | None] = mapped_column(Float)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MutantTestSuggestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """AI-written tests for the survivors of a mutation run, each checked in the sandbox."""

    __tablename__ = "mutant_test_suggestions"

    mutation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mutation_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "suggestion_status"), nullable=False, default=RunStatus.QUEUED
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    # Serialised SuggestionResult.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
