"""Company backgrounds, composition snapshots and validated PNG export rows.

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    op.create_table(
        "brand_backgrounds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("filename", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("source_model", sa.String(150), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("product_id", "id", name="uq_brand_background_owner_id"),
        sa.UniqueConstraint("product_id", "sha256", name="uq_brand_background_digest"),
    )
    op.create_index("ix_brand_backgrounds_product_id", "brand_backgrounds", ["product_id"])
    op.create_table(
        "brand_compositions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("background_id", sa.Uuid(), nullable=True),
        sa.Column("frame_key", sa.String(80), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("brand_snapshot", sa.JSON(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("product_id", "id", name="uq_brand_composition_owner_id"),
        sa.UniqueConstraint("product_id", "frame_key", name="uq_brand_composition_key"),
        sa.ForeignKeyConstraint(
            ["product_id", "version_id"],
            ["brand_kit_versions.product_id", "brand_kit_versions.id"],
            name="fk_brand_composition_owner_version"),
        sa.ForeignKeyConstraint(
            ["product_id", "background_id"],
            ["brand_backgrounds.product_id", "brand_backgrounds.id"],
            name="fk_brand_composition_owner_background"),
    )
    op.create_index("ix_brand_compositions_product_id", "brand_compositions", ["product_id"])
    op.create_table(
        "brand_renders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("composition_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(80), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("product_id", "id", name="uq_brand_render_owner_id"),
        sa.UniqueConstraint("product_id", "composition_id", name="uq_brand_render_composition"),
        sa.ForeignKeyConstraint(
            ["product_id", "composition_id"],
            ["brand_compositions.product_id", "brand_compositions.id"],
            name="fk_brand_render_owner_composition"),
    )
    op.create_index("ix_brand_renders_product_id", "brand_renders", ["product_id"])


def downgrade():
    op.drop_index("ix_brand_renders_product_id", "brand_renders")
    op.drop_table("brand_renders")
    op.drop_index("ix_brand_compositions_product_id", "brand_compositions")
    op.drop_table("brand_compositions")
    op.drop_index("ix_brand_backgrounds_product_id", "brand_backgrounds")
    op.drop_table("brand_backgrounds")
