"""failure analysis: deterministic groups per execution, AI analysis jobs

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    op.add_column(
        "test_executions",
        sa.Column("failures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_table(
        "failure_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="failure_analysis_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_failure_analyses")),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["test_executions.id"],
            name=op.f("fk_failure_analyses_execution_id_test_executions"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(repr(s) for s in STATUSES) + ")",
            name=op.f("ck_failure_analyses_failure_analysis_status"),
        ),
    )
    op.create_index(
        op.f("ix_failure_analyses_execution_id"), "failure_analyses", ["execution_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_failure_analyses_execution_id"), table_name="failure_analyses")
    op.drop_table("failure_analyses")
    op.drop_column("test_executions", "failures")
