"""Explicit per-company SEO factory opt-in. Existing and demo companies remain OFF."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seo_factory_activations",
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by", sa.String(200), nullable=False),
    )
    # No rows inserted. Absence is interpreted as disabled.


def downgrade():
    op.drop_table("seo_factory_activations")
