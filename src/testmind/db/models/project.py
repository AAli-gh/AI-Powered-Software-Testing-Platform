from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from testmind.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum
from testmind.domain.enums import SourceType

if TYPE_CHECKING:
    from testmind.db.models.analysis_run import AnalysisRun


class Project(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(
        str_enum(SourceType, "source_type"), nullable=False
    )
    # For local sources: POSIX path relative to TESTMIND_REPOS_ROOT (never absolute), so the
    # same row is valid for the API and the worker regardless of where the root is mounted.
    source_location: Mapped[str] = mapped_column(String(1024), nullable=False)

    runs: Mapped[list[AnalysisRun]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AnalysisRun.created_at.desc()",
    )
