"""Tenant-isolated immutable assets and results for programmatic Stories composition."""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin, TimestampMixin


class BrandBackground(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_backgrounds"
    __table_args__ = (
        UniqueConstraint("product_id", "id", name="uq_brand_background_owner_id"),
        UniqueConstraint("product_id", "sha256", name="uq_brand_background_digest"),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(80), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="upload")
    source_model: Mapped[str | None] = mapped_column(String(150))


class BrandComposition(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_compositions"
    __table_args__ = (
        UniqueConstraint("product_id", "id", name="uq_brand_composition_owner_id"),
        UniqueConstraint("product_id", "frame_key", name="uq_brand_composition_key"),
        ForeignKeyConstraint(
            ["product_id", "version_id"],
            ["brand_kit_versions.product_id", "brand_kit_versions.id"],
            name="fk_brand_composition_owner_version",
        ),
        ForeignKeyConstraint(
            ["product_id", "background_id"],
            ["brand_backgrounds.product_id", "brand_backgrounds.id"],
            name="fk_brand_composition_owner_background",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    version_id: Mapped[UUID] = mapped_column(nullable=False)
    background_id: Mapped[UUID | None] = mapped_column(nullable=True)
    frame_key: Mapped[str] = mapped_column(String(80), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    spec: Mapped[dict] = mapped_column(JSON, nullable=False)
    brand_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)


class BrandRender(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "brand_renders"
    __table_args__ = (
        UniqueConstraint("product_id", "id", name="uq_brand_render_owner_id"),
        UniqueConstraint("product_id", "composition_id", name="uq_brand_render_composition"),
        ForeignKeyConstraint(
            ["product_id", "composition_id"],
            ["brand_compositions.product_id", "brand_compositions.id"],
            name="fk_brand_render_owner_composition",
        ),
    )

    product_id: Mapped[UUID] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    composition_id: Mapped[UUID] = mapped_column(nullable=False)
    filename: Mapped[str] = mapped_column(String(80), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
