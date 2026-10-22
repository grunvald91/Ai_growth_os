"""Which ServGo business belongs to which studio company, and which one-time links were already used."""
from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class StudioBusinessLink(UUIDMixin, TimestampMixin, Base):
    """One ServGo business opens exactly one studio company. Set by the studio owner, never by the person signing in."""
    __tablename__ = "studio_business_links"

    servgo_business_id: Mapped[int] = mapped_column(Integer, unique=True, index=True, nullable=False)
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)


class StudioSsoTicket(UUIDMixin, Base):
    """A one-time link that was already exchanged. The unique key makes a second use impossible."""
    __tablename__ = "studio_sso_tickets"

    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # The company the link opened; removing the company removes its used-link records too.
    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), index=True, nullable=False)
    servgo_user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    servgo_business_id: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
