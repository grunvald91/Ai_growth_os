"""Owner conversation with the dispatcher: one thread per company and account.

A user message and the dispatcher's answer are separate rows. The answer row also
holds the dispatcher's turn (intent, capability, parameters, confidence), so each
turn stays traceable to the Task, ToolCall and CostRecord it produced.
"""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class DispatcherConversation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "dispatcher_conversations"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    # Studio login of the person talking (a company may have several accounts).
    account: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="open", nullable=False)


class DispatcherMessage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "dispatcher_messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_dispatcher_message_seq"),
        # A retry from a bad mobile connection carries the same key and must not repeat the action.
        UniqueConstraint("conversation_id", "client_key", name="uq_dispatcher_message_key"),
    )

    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("dispatcher_conversations.id"), index=True, nullable=False)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user | assistant
    input_mode: Mapped[str] = mapped_column(String(20), default="text", nullable=False)  # text | chip | voice | confirm
    text: Mapped[str] = mapped_column(Text, default="", nullable=False)
    spoken: Mapped[str | None] = mapped_column(Text)
    blocks: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(20))
    capability: Mapped[str | None] = mapped_column(String(60))
    params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    model: Mapped[str | None] = mapped_column(String(200))
    client_key: Mapped[str | None] = mapped_column(String(100))
    # Pending confirmation cards: usable once, by the same conversation only.
    confirmation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    confirmation_state: Mapped[str | None] = mapped_column(String(20))  # pending | confirmed | declined
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
