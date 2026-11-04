"""Brand Kit asset library, immutable drafts and company-scoped approved pointer.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    op.create_table(
        "brand_assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("filename", sa.String(100), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("product_id", "id", name="uq_brand_asset_owner_id"),
    )
    op.create_index("ix_brand_assets_product_id", "brand_assets", ["product_id"])
    op.create_table(
        "brand_kit_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("product_id", "version", name="uq_brand_kit_product_version"),
        sa.UniqueConstraint("product_id", "id", name="uq_brand_kit_owner_id"),
    )
    op.create_index("ix_brand_kit_versions_product_id", "brand_kit_versions", ["product_id"])
    op.create_table(
        "brand_kit_active",
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), primary_key=True),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["product_id", "version_id"],
            ["brand_kit_versions.product_id", "brand_kit_versions.id"],
            name="fk_brand_active_same_company_version",
        ),
    )


def downgrade():
    op.drop_table("brand_kit_active")
    op.drop_index("ix_brand_kit_versions_product_id", "brand_kit_versions")
    op.drop_table("brand_kit_versions")
    op.drop_index("ix_brand_assets_product_id", "brand_assets")
    op.drop_table("brand_assets")
