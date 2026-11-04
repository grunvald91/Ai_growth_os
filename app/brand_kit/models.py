"""Immutable Brand Kit snapshots and private company asset metadata."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDMixin


class BrandAsset(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_assets"
    __table_args__ = (
        UniqueConstraint("product_id", "id", name="uq_brand_asset_owner_id"),
        UniqueConstraint("product_id", "kind", "sha256", name="uq_brand_asset_dedupe"),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    filename: Mapped[str] = mapped_column(String(100), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)


class BrandVersion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_kit_versions"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_brand_kit_product_version"),
        UniqueConstraint("product_id", "id", name="uq_brand_kit_owner_id"),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    config: Mapped[dict] = mapped_column(JSON, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BrandActive(TimestampMixin, Base):
    __tablename__ = "brand_kit_active"
    __table_args__ = (
        ForeignKeyConstraint(
            ["product_id", "version_id"],
            ["brand_kit_versions.product_id", "brand_kit_versions.id"],
            name="fk_brand_active_same_company_version",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), primary_key=True)
    version_id: Mapped[UUID] = mapped_column(nullable=False)
