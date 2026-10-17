"""Product-scoped comment channels and unique inbound comments."""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def common():
    return [sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False)]


def upgrade():
    op.create_table("comment_channels", *common(),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("account_id", sa.String(200), nullable=False),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("cursor", sa.JSON(), nullable=False),
        sa.UniqueConstraint("product_id", "platform", name="uq_comment_channel"))
    op.create_table("business_comments", *common(),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(250), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("post_text", sa.Text(), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reply_id", sa.Uuid(), sa.ForeignKey("artifacts.id")),
        sa.Column("external_reply_id", sa.String(250)),
        sa.UniqueConstraint("product_id", "platform", "external_id", name="uq_business_comment"))
    op.create_index("ix_comment_channels_product_id", "comment_channels", ["product_id"])
    op.create_index("ix_business_comments_product_id", "business_comments", ["product_id"])


def downgrade():
    op.drop_table("business_comments")
    op.drop_table("comment_channels")
