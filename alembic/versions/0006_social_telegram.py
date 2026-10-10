"""Isolated Telegram destinations and scheduled deliveries."""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def base():
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=False),
    ]


def upgrade():
    op.create_table("social_telegram_channels", *base(),
        sa.Column("chat_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("username", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("auto_plan", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("chat_id", name="uq_social_telegram_chat"))
    op.create_index("ix_social_telegram_channels_product_id", "social_telegram_channels", ["product_id"])
    op.create_table("social_telegram_media", *base(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("file_name", sa.String(100), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False))
    op.create_index("ix_social_telegram_media_product_id", "social_telegram_media", ["product_id"])
    op.create_table("social_telegram_posts", *base(),
        sa.Column("channel_id", sa.Uuid(), sa.ForeignKey("social_telegram_channels.id"), nullable=False),
        sa.Column("plan_slot_key", sa.String(120)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("media", sa.JSON(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("external_message_ids", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(100)),
        sa.UniqueConstraint("channel_id", "plan_slot_key", name="uq_telegram_plan_slot"))
    op.create_index("ix_social_telegram_posts_product_id", "social_telegram_posts", ["product_id"])
    op.create_index("ix_social_telegram_posts_channel_id", "social_telegram_posts", ["channel_id"])
    op.create_index("ix_social_telegram_posts_scheduled_at", "social_telegram_posts", ["scheduled_at"])

    op.create_table("social_oauth_attempts", *base(),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("state_digest", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.UniqueConstraint("state_digest", name="uq_social_oauth_state"))
    op.create_index("ix_social_oauth_attempts_product_id", "social_oauth_attempts", ["product_id"])


def downgrade():
    op.drop_table("social_oauth_attempts")
    op.drop_table("social_telegram_posts")
    op.drop_table("social_telegram_media")
    op.drop_table("social_telegram_channels")
