"""Add independent Studio account tickets and workspace ownership."""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("studio_account_tickets",
        sa.Column("jti", sa.String(64), primary_key=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("studio_account_products",
        sa.Column("servgo_user_id", sa.Integer(), primary_key=True),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True))


def downgrade():
    op.drop_table("studio_account_products")
    op.drop_table("studio_account_tickets")
