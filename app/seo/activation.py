"""SEO factory is explicitly OFF for every product until an authorized owner opts in.

Activation belongs to a product, not to a worker process, demo seed or login.
Missing row = disabled. Disabling cancels *queued* SEO work only; running calls
may finish, but later queued generation and publication are checked again.
"""
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Uuid, select, update, or_
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base
from app.models.domain import Product, Task, TaskStatus

SEO_TASK_PREFIX = "SEO-ARTICLE:"


class SeoFactoryActivation(Base):
    __tablename__ = "seo_factory_activations"

    product_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    changed_by: Mapped[str] = mapped_column(String(200), nullable=False)


def is_enabled(db: Session, product_id: UUID) -> bool:
    """No implicit opt-in, including all pre-existing and demo companies."""
    return db.scalar(select(SeoFactoryActivation.enabled).where(
        SeoFactoryActivation.product_id == product_id)) is True


def enabled_product_ids(db: Session) -> set[UUID]:
    return set(db.scalars(select(SeoFactoryActivation.product_id).where(
        SeoFactoryActivation.enabled.is_(True))).all())


def require_enabled(db: Session, product_id: UUID) -> None:
    if not is_enabled(db, product_id):
        raise ValueError("SEO-завод выключен для этой компании. Подключите его в разделе SEO-завод.")


def set_enabled(db: Session, product_id: UUID, *, enabled: bool, actor: str) -> bool:
    """Serializable with SEO producer paths through the locked product row."""
    product = db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    if product is None:
        raise LookupError("Компания не найдена")
    row = db.get(SeoFactoryActivation, product_id)
    if row is None:
        row = SeoFactoryActivation(product_id=product_id, enabled=enabled,
                                   changed_at=datetime.now(timezone.utc), changed_by=actor[:200])
        db.add(row)
    elif row.enabled != enabled:
        row.enabled = enabled
        row.changed_at = datetime.now(timezone.utc)
        row.changed_by = actor[:200]
    else:
        return enabled
    if not enabled:
        # Only this company's pending SEO tasks; other marketing content is untouched.
        db.execute(update(Task).where(
            Task.product_id == product_id,
            Task.status == TaskStatus.QUEUED,
            or_(Task.assigned_agent == "seo_factory",
                Task.title.like(f"{SEO_TASK_PREFIX}%")),
        ).values(status=TaskStatus.CANCELLED))
    db.commit()
    return enabled
