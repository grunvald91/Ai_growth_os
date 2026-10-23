"""How many days back the business analysis looks, per company."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("insight_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("depth_days", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(200)))
    op.create_index("ix_insight_settings_product_id", "insight_settings", ["product_id"], unique=True)


def downgrade():
    op.drop_index("ix_insight_settings_product_id", table_name="insight_settings")
    op.drop_table("insight_settings")
