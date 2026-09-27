"""GitHub integration: imported projects, pull request analyses and imports

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-27
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def _status(table: str, name: str) -> list[Any]:
    values = ", ".join(repr(s) for s in STATUSES)
    return [
        sa.Column(
            "status", sa.Enum(*STATUSES, name=name, native_enum=False, length=32), nullable=False
        ),
        sa.CheckConstraint(f"status IN ({values})", name=op.f(f"ck_{table}_{name}")),
    ]


def _job_columns() -> list[Any]:
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


def upgrade() -> None:
    op.drop_constraint(op.f("ck_projects_source_type"), "projects", type_="check")
    op.create_check_constraint(
        op.f("ck_projects_source_type"), "projects", "source_type IN ('local', 'github')"
    )
    op.add_column("projects", sa.Column("remote_repository", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("remote_ref", sa.String(200), nullable=True))
    op.add_column("projects", sa.Column("remote_commit", sa.String(40), nullable=True))

    op.create_table(
        "github_pull_analyses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("delivery_id", sa.String(100), nullable=True),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("pull_number", sa.Integer(), nullable=False),
        sa.Column("head_sha", sa.String(40), nullable=True),
        sa.Column("base_sha", sa.String(40), nullable=True),
        *_status("github_pull_analyses", "pull_analysis_status"),
        sa.Column("conclusion", sa.String(20), nullable=True),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("check_run_id", sa.BigInteger(), nullable=True),
        sa.Column("comment_id", sa.BigInteger(), nullable=True),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_job_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_pull_analyses")),
        sa.UniqueConstraint("delivery_id", name=op.f("uq_github_pull_analyses_delivery_id")),
    )
    op.create_index(
        op.f("ix_github_pull_analyses_repository"), "github_pull_analyses", ["repository"]
    )
    op.create_table(
        "github_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=False),
        sa.Column("repository", sa.String(200), nullable=False),
        sa.Column("ref", sa.String(200), nullable=False),
        *_status("github_imports", "import_status"),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("project_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("skipped", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        *_job_columns(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_imports")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_github_imports_project_id_projects"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["analysis_runs.id"],
            name=op.f("fk_github_imports_run_id_analysis_runs"),
            ondelete="SET NULL",
        ),
    )


def downgrade() -> None:
    op.drop_table("github_imports")
    op.drop_index(op.f("ix_github_pull_analyses_repository"), table_name="github_pull_analyses")
    op.drop_table("github_pull_analyses")
    op.drop_column("projects", "remote_commit")
    op.drop_column("projects", "remote_ref")
    op.drop_column("projects", "remote_repository")
    op.execute("DELETE FROM projects WHERE source_type = 'github'")
    op.drop_constraint(op.f("ck_projects_source_type"), "projects", type_="check")
    op.create_check_constraint(
        op.f("ck_projects_source_type"), "projects", "source_type IN ('local')"
    )
