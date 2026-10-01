"""Add complete experiment contracts.

Revision ID: 0002
Revises: 0001
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("experiment_key", sa.String(length=100), nullable=True))
    op.add_column("experiments", sa.Column("action", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("secondary_signals", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    op.add_column("experiments", sa.Column("attribution_method", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("baseline_summary", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("observation_window_days", sa.Integer(), nullable=True))
    op.add_column("experiments", sa.Column("time_budget_minutes", sa.Integer(), nullable=True))
    op.add_column("experiments", sa.Column("stop_condition", sa.Text(), nullable=True))
    op.add_column("experiments", sa.Column("inconclusive_condition", sa.Text(), nullable=True))
    op.create_unique_constraint(
        "uq_experiments_product_key", "experiments", ["product_id", "experiment_key"]
    )
    op.alter_column("experiments", "secondary_signals", server_default=None)


def downgrade() -> None:
    op.drop_constraint("uq_experiments_product_key", "experiments", type_="unique")
    op.drop_column("experiments", "inconclusive_condition")
    op.drop_column("experiments", "stop_condition")
    op.drop_column("experiments", "time_budget_minutes")
    op.drop_column("experiments", "observation_window_days")
    op.drop_column("experiments", "baseline_summary")
    op.drop_column("experiments", "attribution_method")
    op.drop_column("experiments", "secondary_signals")
    op.drop_column("experiments", "action")
    op.drop_column("experiments", "experiment_key")
