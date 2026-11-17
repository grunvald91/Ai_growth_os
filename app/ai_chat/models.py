"""Database models for ServGo AI Chat.

Conversations, messages, attachments and paid provider request logs are strictly
scoped by owner_account (and product_id when associated with a company).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class AiChatConversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_chat_conversations"
    __table_args__ = (
        Index("ix_ai_chat_conv_owner_recent", "owner_account", "last_message_at", "updated_at"),
    )

    owner_account: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="Новый чат", nullable=False)
    selected_model: Mapped[str | None] = mapped_column(String(150), nullable=True)
    specialist_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)


class AiChatMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_chat_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_ai_chat_message_seq"),
        UniqueConstraint("conversation_id", "client_key", name="uq_ai_chat_message_client_key"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    mode: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text | image
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="completed", nullable=False)
    quote_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="SET NULL"), index=True, nullable=True,
    )
    quote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_images: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context_truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    context_omitted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_key: Mapped[str | None] = mapped_column(String(100), nullable=True)


class AiChatAttachment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_chat_attachments"

    owner_account: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True, nullable=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), index=True, nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(30), default="upload", nullable=False)  # upload | generated_image
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(30), default="ready", nullable=False)
    extraction_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AiChatRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "ai_chat_requests"
    __table_args__ = (
        UniqueConstraint("conversation_id", "idempotency_key", name="uq_ai_chat_request_idempotency"),
        Index("ix_ai_chat_request_owner_created", "owner_account", "created_at"),
    )

    owner_account: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    product_id: Mapped[UUID | None] = mapped_column(ForeignKey("products.id"), index=True, nullable=True)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_chat_conversations.id", ondelete="CASCADE"), index=True, nullable=False,
    )
    user_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), index=True, nullable=True,
    )
    assistant_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_chat_messages.id", ondelete="CASCADE"), index=True, nullable=True,
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)  # text | image
    model_id: Mapped[str] = mapped_column(String(150), nullable=False)
    provider: Mapped[str] = mapped_column(String(60), default="claudehub", nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="queued", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
