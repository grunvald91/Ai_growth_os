from uuid import UUID

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class CommentChannel(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "comment_channels"
    __table_args__ = (UniqueConstraint("product_id", "platform", name="uq_comment_channel"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    account_id: Mapped[str] = mapped_column(String(200))
    account_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="connected")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)


class BusinessComment(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "business_comments"
    __table_args__ = (UniqueConstraint("product_id", "platform", "external_id", name="uq_business_comment"),)

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(250))
    text: Mapped[str] = mapped_column(Text)
    post_text: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[dict] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(30), default="manual")
    status: Mapped[str] = mapped_column(String(30), default="new")
    reply_id: Mapped[UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    external_reply_id: Mapped[str | None] = mapped_column(String(250))
