"""Per-business Telegram destinations and durable, per-destination delivery records."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDMixin


class TelegramChannel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "social_telegram_channels"
    __table_args__ = (UniqueConstraint("chat_id", name="uq_social_telegram_chat"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    auto_plan: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class TelegramPost(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "social_telegram_posts"
    __table_args__ = (UniqueConstraint("channel_id", "plan_slot_key", name="uq_telegram_plan_slot"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    channel_id: Mapped[UUID] = mapped_column(ForeignKey("social_telegram_channels.id"), index=True, nullable=False)
    plan_slot_key: Mapped[str | None] = mapped_column(String(120))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", nullable=False)
    external_message_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))


class TelegramMedia(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "social_telegram_media"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    file_name: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)


class SocialOAuthAttempt(UUIDMixin, TimestampMixin, Base):
    """Short lived one-time browser OAuth state; no provider tokens in the database."""
    __tablename__ = "social_oauth_attempts"
    __table_args__ = (UniqueConstraint("state_digest", name="uq_social_oauth_state"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    state_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)


class TelegramPlanMedia(UUIDMixin, TimestampMixin, Base):
    """Uploaded photo/video assignment for a specific editorial plan slot."""
    __tablename__ = "social_telegram_plan_media"
    __table_args__ = (UniqueConstraint("plan_id", "slot_id", "position", name="uq_telegram_plan_media_position"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("artifacts.id"), index=True, nullable=False)
    slot_id: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    media_id: Mapped[UUID] = mapped_column(ForeignKey("social_telegram_media.id"), nullable=False)
