"""create projects and analysis runs

Revision ID: 0001
Revises:
Create Date: 2026-09-25

Enum columns are stored as VARCHAR with a single, explicitly named CHECK constraint
(see testmind.db.base.str_enum). `create_constraint=False` on the column type prevents
a duplicate constraint from being emitted alongside the explicit one.
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_TYPES = ("local",)
RUN_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def _in_check(column: str, values: Sequence[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


def _timestamps() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "source_type",
            sa.Enum(*SOURCE_TYPES, name="source_type", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("source_location", sa.String(length=1024), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("name", name=op.f("uq_projects_name")),
        sa.CheckConstraint(
            _in_check("source_type", SOURCE_TYPES), name=op.f("ck_projects_source_type")
        ),
    )

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*RUN_STATUSES, name="run_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_analysis_runs")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_analysis_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            _in_check("status", RUN_STATUSES), name=op.f("ck_analysis_runs_run_status")
        ),
    )
    op.create_index(op.f("ix_analysis_runs_project_id"), "analysis_runs", ["project_id"])
    op.create_index(op.f("ix_analysis_runs_status"), "analysis_runs", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_analysis_runs_status"), table_name="analysis_runs")
    op.drop_index(op.f("ix_analysis_runs_project_id"), table_name="analysis_runs")
    op.drop_table("analysis_runs")
    op.drop_table("projects")
