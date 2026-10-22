"""Sign-in from the ServGo admin: business links and used one-time links."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("studio_business_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("servgo_business_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False))
    op.create_index("ix_studio_business_links_servgo_business_id", "studio_business_links",
                    ["servgo_business_id"], unique=True)
    op.create_index("ix_studio_business_links_product_id", "studio_business_links", ["product_id"])
    op.create_table("studio_sso_tickets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("jti", sa.String(64), nullable=False, unique=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("servgo_user_id", sa.Integer(), nullable=False),
        sa.Column("servgo_business_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_studio_sso_tickets_product_id", "studio_sso_tickets", ["product_id"])


def downgrade():
    op.drop_index("ix_studio_sso_tickets_product_id", table_name="studio_sso_tickets")
    op.drop_table("studio_sso_tickets")
    op.drop_index("ix_studio_business_links_product_id", table_name="studio_business_links")
    op.drop_index("ix_studio_business_links_servgo_business_id", table_name="studio_business_links")
    op.drop_table("studio_business_links")
