from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import RunStatus


class GitHubPullAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Analysis of one head commit of a pull request, posted back as a check run + comment."""

    __tablename__ = "github_pull_analyses"

    # The webhook delivery that asked for it (GitHub re-delivers: one job per delivery).
    delivery_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    pull_number: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str | None] = mapped_column(String(40))
    base_sha: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "pull_analysis_status"), nullable=False, default=RunStatus.QUEUED
    )
    conclusion: Mapped[str | None] = mapped_column(String(20))
    # Serialised PullReport.
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    check_run_id: Mapped[int | None] = mapped_column(BigInteger)
    comment_id: Mapped[int | None] = mapped_column(BigInteger)
    # Issues created from the report's suggestions: [{suggestion, number, url}].
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitHubImport(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A repository imported from GitHub at a ref, as a project snapshot."""

    __tablename__ = "github_imports"

    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    repository: Mapped[str] = mapped_column(String(200), nullable=False)
    ref: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        str_enum(RunStatus, "import_status"), nullable=False, default=RunStatus.QUEUED
    )
    commit_sha: Mapped[str | None] = mapped_column(String(40))
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL")
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL")
    )
    skipped: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
