"""How far back the analysis looks, kept per company."""
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class InsightSettings(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "insight_settings"

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), unique=True, index=True, nullable=False)
    depth_days: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(200))
