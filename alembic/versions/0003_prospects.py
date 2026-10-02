"""Add canonical prospects for outreach experiments.

Revision ID: 0003
Revises: 0002
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "prospects",
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("experiment_id", sa.Uuid(), nullable=False),
        sa.Column("lead_key", sa.String(length=100), nullable=False),
        sa.Column("business_name", sa.String(length=300), nullable=False),
        sa.Column("area", sa.String(length=100), nullable=True),
        sa.Column("category", sa.String(length=200), nullable=True),
        sa.Column("public_phone", sa.String(length=50), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("source_place_id", sa.String(length=200), nullable=True),
        sa.Column("source_checked_at", sa.Date(), nullable=True),
        sa.Column("fit_status", sa.String(length=50), nullable=False, server_default="needs_validation"),
        sa.Column("validation_status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("outreach_status", sa.String(length=50), nullable=False, server_default="not_contacted"),
        sa.Column("preferred_channel", sa.String(length=50), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("whatsapp", sa.String(length=100), nullable=True),
        sa.Column("telegram", sa.String(length=200), nullable=True),
        sa.Column("website", sa.String(length=500), nullable=True),
        sa.Column("tracking_url", sa.String(length=1000), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("experiment_id", "lead_key", name="uq_prospects_experiment_lead"),
    )
    op.create_index(op.f("ix_prospects_product_id"), "prospects", ["product_id"], unique=False)
    op.create_index(op.f("ix_prospects_experiment_id"), "prospects", ["experiment_id"], unique=False)
    op.alter_column("prospects", "fit_status", server_default=None)
    op.alter_column("prospects", "validation_status", server_default=None)
    op.alter_column("prospects", "outreach_status", server_default=None)
    op.alter_column("prospects", "evidence", server_default=None)


def downgrade() -> None:
    op.drop_index(op.f("ix_prospects_experiment_id"), table_name="prospects")
    op.drop_index(op.f("ix_prospects_product_id"), table_name="prospects")
    op.drop_table("prospects")
