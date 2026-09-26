"""create test executions

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
OUTCOMES = (
    "passed", "failed", "error", "no_tests", "timed_out", "install_failed", "sandbox_unavailable",
)  # fmt: skip


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def upgrade() -> None:
    op.create_table(
        "test_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="execution_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(*OUTCOMES, name="execution_outcome", native_enum=False, length=32),
            nullable=True,
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_executions")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_test_executions_run_id_analysis_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["test_generations.id"],
            name=op.f("fk_test_executions_generation_id_test_generations"),
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in("status", STATUSES), name=op.f("ck_test_executions_execution_status")
        ),
        sa.CheckConstraint(
            _in("outcome", OUTCOMES), name=op.f("ck_test_executions_execution_outcome")
        ),
    )
    op.create_index(op.f("ix_test_executions_run_id"), "test_executions", ["run_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_test_executions_run_id"), table_name="test_executions")
    op.drop_table("test_executions")
