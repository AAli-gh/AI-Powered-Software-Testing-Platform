from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus


class TestGeneration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One AI test-generation job for a (succeeded) analysis run."""

    __tablename__ = "test_generations"
    __test__ = False  # not a pytest test class

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "generation_status"), nullable=False, default=RunStatus.QUEUED
    )
    # Serialised GenerationOptions as requested.
    options: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    # Serialised GenerationResult.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
