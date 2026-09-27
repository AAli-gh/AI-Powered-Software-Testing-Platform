"""create mutation runs and mutant test suggestions

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def _status(name: str) -> sa.Column[str]:
    return sa.Column(
        "status", sa.Enum(*STATUSES, name=name, native_enum=False, length=32), nullable=False
    )


def _timestamps() -> list[sa.Column[Any]]:
    return [
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
    ]


def _check(table: str, name: str) -> sa.CheckConstraint:
    values = ", ".join(repr(s) for s in STATUSES)
    return sa.CheckConstraint(f"status IN ({values})", name=op.f(f"ck_{table}_{name}"))


def upgrade() -> None:
    op.create_table(
        "mutation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        _status("mutation_status"),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mutation_runs")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_mutation_runs_run_id_analysis_runs"),
            ondelete="CASCADE",
        ),
        _check("mutation_runs", "mutation_status"),
    )
    op.create_index(op.f("ix_mutation_runs_run_id"), "mutation_runs", ["run_id"])
    op.create_table(
        "mutant_test_suggestions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("mutation_run_id", sa.Uuid(), nullable=False),
        _status("suggestion_status"),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=20), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mutant_test_suggestions")),
        sa.ForeignKeyConstraint(
            ["mutation_run_id"],
            ["mutation_runs.id"],
            name=op.f("fk_mutant_test_suggestions_mutation_run_id_mutation_runs"),
            ondelete="CASCADE",
        ),
        _check("mutant_test_suggestions", "suggestion_status"),
    )
    op.create_index(
        op.f("ix_mutant_test_suggestions_mutation_run_id"),
        "mutant_test_suggestions",
        ["mutation_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_mutant_test_suggestions_mutation_run_id"), table_name="mutant_test_suggestions"
    )
    op.drop_table("mutant_test_suggestions")
    op.drop_index(op.f("ix_mutation_runs_run_id"), table_name="mutation_runs")
    op.drop_table("mutation_runs")
