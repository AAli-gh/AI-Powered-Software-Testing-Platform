"""create regressions

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
MODES = ("full", "selected")
VERDICTS = ("regressions", "clean", "inconclusive")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def upgrade() -> None:
    op.create_table(
        "regressions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("status", _enum(STATUSES, "regression_status"), nullable=False),
        sa.Column("mode", _enum(MODES, "regression_mode"), nullable=False),
        sa.Column("base_ref", sa.String(length=200), nullable=False),
        sa.Column("head_ref", sa.String(length=200), nullable=False),
        sa.Column("base_commit", sa.String(length=40), nullable=False),
        sa.Column("head_commit", sa.String(length=40), nullable=False),
        sa.Column("verdict", _enum(VERDICTS, "regression_verdict"), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_regressions")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_regressions_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_regressions_regression_status")),
        sa.CheckConstraint(_in("mode", MODES), name=op.f("ck_regressions_regression_mode")),
        sa.CheckConstraint(
            _in("verdict", VERDICTS), name=op.f("ck_regressions_regression_verdict")
        ),
    )
    op.create_index(op.f("ix_regressions_project_id"), "regressions", ["project_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_regressions_project_id"), table_name="regressions")
    op.drop_table("regressions")
