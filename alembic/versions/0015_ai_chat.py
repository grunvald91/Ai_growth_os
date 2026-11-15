"""ServGo AI Chat tables: conversations, messages, attachments and request logs.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def timestamps():
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def upgrade():
    op.create_table(
        "ai_chat_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_account", sa.String(200), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column("title", sa.String(200), nullable=False, server_default="Новый чат"),
        sa.Column("selected_model", sa.String(150), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
    )
    op.create_index("ix_ai_chat_conversations_owner_account", "ai_chat_conversations", ["owner_account"])
    op.create_index("ix_ai_chat_conversations_product_id", "ai_chat_conversations", ["product_id"])
    op.create_index("ix_ai_chat_conversations_last_message_at", "ai_chat_conversations", ["last_message_at"])
    op.create_index(
        "ix_ai_chat_conv_owner_recent",
        "ai_chat_conversations",
        ["owner_account", "last_message_at", "updated_at"],
    )

    op.create_table(
        "ai_chat_messages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False, server_default="text"),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("model_id", sa.String(150), nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="completed"),
        sa.Column(
            "quote_message_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_messages.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("quote_text", sa.Text(), nullable=True),
        sa.Column("generated_images", sa.JSON(), nullable=False),
        sa.Column("context_truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("context_omitted_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("client_key", sa.String(100), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_ai_chat_message_seq"),
        sa.UniqueConstraint("conversation_id", "client_key", name="uq_ai_chat_message_client_key"),
    )
    op.create_index("ix_ai_chat_messages_conversation_id", "ai_chat_messages", ["conversation_id"])
    op.create_index("ix_ai_chat_messages_quote_message_id", "ai_chat_messages", ["quote_message_id"])

    op.create_table(
        "ai_chat_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_account", sa.String(200), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False, server_default="upload"),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("mime_type", sa.String(120), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(30), nullable=False, server_default="ready"),
        sa.Column("extraction_note", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        *timestamps(),
    )
    op.create_index("ix_ai_chat_attachments_owner_account", "ai_chat_attachments", ["owner_account"])
    op.create_index("ix_ai_chat_attachments_product_id", "ai_chat_attachments", ["product_id"])
    op.create_index("ix_ai_chat_attachments_conversation_id", "ai_chat_attachments", ["conversation_id"])
    op.create_index("ix_ai_chat_attachments_message_id", "ai_chat_attachments", ["message_id"])

    op.create_table(
        "ai_chat_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_account", sa.String(200), nullable=False),
        sa.Column("product_id", sa.Uuid(), sa.ForeignKey("products.id"), nullable=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_message_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "assistant_message_id",
            sa.Uuid(),
            sa.ForeignKey("ai_chat_messages.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("model_id", sa.String(150), nullable=False),
        sa.Column("provider", sa.String(60), nullable=False, server_default="claudehub"),
        sa.Column("status", sa.String(30), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(120), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("conversation_id", "idempotency_key", name="uq_ai_chat_request_idempotency"),
    )
    op.create_index("ix_ai_chat_requests_owner_account", "ai_chat_requests", ["owner_account"])
    op.create_index("ix_ai_chat_requests_product_id", "ai_chat_requests", ["product_id"])
    op.create_index("ix_ai_chat_requests_conversation_id", "ai_chat_requests", ["conversation_id"])
    op.create_index("ix_ai_chat_requests_user_message_id", "ai_chat_requests", ["user_message_id"])
    op.create_index("ix_ai_chat_requests_assistant_message_id", "ai_chat_requests", ["assistant_message_id"])
    op.create_index("ix_ai_chat_request_owner_created", "ai_chat_requests", ["owner_account", "created_at"])


def downgrade():
    op.drop_index("ix_ai_chat_request_owner_created", "ai_chat_requests")
    op.drop_index("ix_ai_chat_requests_assistant_message_id", "ai_chat_requests")
    op.drop_index("ix_ai_chat_requests_user_message_id", "ai_chat_requests")
    op.drop_index("ix_ai_chat_requests_conversation_id", "ai_chat_requests")
    op.drop_index("ix_ai_chat_requests_product_id", "ai_chat_requests")
    op.drop_index("ix_ai_chat_requests_owner_account", "ai_chat_requests")
    op.drop_table("ai_chat_requests")

    op.drop_index("ix_ai_chat_attachments_message_id", "ai_chat_attachments")
    op.drop_index("ix_ai_chat_attachments_conversation_id", "ai_chat_attachments")
    op.drop_index("ix_ai_chat_attachments_product_id", "ai_chat_attachments")
    op.drop_index("ix_ai_chat_attachments_owner_account", "ai_chat_attachments")
    op.drop_table("ai_chat_attachments")

    op.drop_index("ix_ai_chat_messages_quote_message_id", "ai_chat_messages")
    op.drop_index("ix_ai_chat_messages_conversation_id", "ai_chat_messages")
    op.drop_table("ai_chat_messages")

    op.drop_index("ix_ai_chat_conv_owner_recent", "ai_chat_conversations")
    op.drop_index("ix_ai_chat_conversations_last_message_at", "ai_chat_conversations")
    op.drop_index("ix_ai_chat_conversations_product_id", "ai_chat_conversations")
    op.drop_index("ix_ai_chat_conversations_owner_account", "ai_chat_conversations")
    op.drop_table("ai_chat_conversations")
