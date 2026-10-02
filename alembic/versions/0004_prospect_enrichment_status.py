"""Track prospect enrichment completion.

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "prospects",
        sa.Column(
            "enrichment_status",
            sa.String(length=50),
            nullable=False,
            server_default="pending",
        ),
    )
    op.alter_column("prospects", "enrichment_status", server_default=None)


def downgrade() -> None:
    op.drop_column("prospects", "enrichment_status")
