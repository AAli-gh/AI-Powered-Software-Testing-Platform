"""API testing: API coverage and probes on executions, and AI API test suggestions

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    op.add_column(
        "test_executions",
        sa.Column("api_probes", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "test_executions",
        sa.Column("api_coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("test_executions", sa.Column("api_coverage_percent", sa.Float(), nullable=True))
    values = ", ".join(repr(s) for s in STATUSES)
    op.create_table(
        "api_test_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="api_suggestion_status", native_enum=False, length=32),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_test_suggestions")),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["test_executions.id"],
            name=op.f("fk_api_test_suggestions_execution_id_test_executions"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            f"status IN ({values})", name=op.f("ck_api_test_suggestions_api_suggestion_status")
        ),
    )
    op.create_index(
        op.f("ix_api_test_suggestions_execution_id"), "api_test_suggestions", ["execution_id"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_api_test_suggestions_execution_id"), table_name="api_test_suggestions")
    op.drop_table("api_test_suggestions")
    op.drop_column("test_executions", "api_coverage_percent")
    op.drop_column("test_executions", "api_coverage")
    op.drop_column("test_executions", "api_probes")
