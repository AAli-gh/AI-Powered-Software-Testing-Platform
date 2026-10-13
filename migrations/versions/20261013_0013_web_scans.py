"""Web scans: crawl a website and record the issues found

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")


def upgrade() -> None:
    values = ", ".join(repr(s) for s in STATUSES)
    op.create_table(
        "web_scans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_url", sa.String(length=2048), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*STATUSES, name="web_scan_status", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column("max_pages", sa.Integer(), nullable=False),
        sa.Column("max_depth", sa.Integer(), nullable=False),
        sa.Column("interact", sa.Boolean(), nullable=False),
        sa.Column("allow_internal", sa.Boolean(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_web_scans")),
        sa.CheckConstraint(f"status IN ({values})", name=op.f("ck_web_scans_web_scan_status")),
    )


def downgrade() -> None:
    op.drop_table("web_scans")
