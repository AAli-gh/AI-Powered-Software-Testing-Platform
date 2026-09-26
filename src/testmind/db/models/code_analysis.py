from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CodeAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Result of the code analysis (symbols, complexity, findings) performed by one run."""

    __tablename__ = "code_analyses"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    analyzer_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Serialised testmind.analyzers.code.CodeProfile.
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
