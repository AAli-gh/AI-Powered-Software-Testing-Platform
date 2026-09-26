"""add coverage to test executions

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "test_executions",
        sa.Column("coverage", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("test_executions", sa.Column("coverage_percent", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("test_executions", "coverage_percent")
    op.drop_column("test_executions", "coverage")
