"""SEO factory is explicitly OFF for every product until an authorized owner opts in.

Activation belongs to a product, not to a worker process, demo seed or login.
Missing row = disabled. Disabling cancels *queued* SEO work only; running calls
may finish, but later queued generation and publication are checked again.
"""
from datetime import datetime, timezone
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Uuid, select, update, or_
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
    # Nullable overrides preserve the previous settings of all existing tenants.
    timezone_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    autorun_interval_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    autorun_horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    publications_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)
    articles_per_day: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    # Cancel dormant SEO tasks both on connection and disconnection;
    # the opt-in never resumes pre-existing jobs and other work is untouched.
    db.execute(update(Task).where(
        Task.product_id == product_id,
        Task.status == TaskStatus.QUEUED,
        or_(Task.assigned_agent == "seo_factory",
            Task.title.like(f"{SEO_TASK_PREFIX}%")),
    ).values(status=TaskStatus.CANCELLED))
    db.commit()
    return enabled


@dataclass(frozen=True)
class SeoEditorialPreferences:
    timezone_name: str = "Europe/Moscow"
    autorun_interval_days: int = 7
    autorun_horizon_days: int = 7
    publications_per_day: int = 3
    articles_per_day: int = 30


def editorial_preferences(db: Session, product_id: UUID, *,
                          default_interval_days: int = 7,
                          default_publications_per_day: int = 3,
                          default_articles_per_day: int = 30) -> SeoEditorialPreferences:
    """Return ONLY this company's overrides, falling back to global defaults."""
    row = db.get(SeoFactoryActivation, product_id)
    if row is None:
        return SeoEditorialPreferences(autorun_interval_days=default_interval_days,
            publications_per_day=default_publications_per_day, articles_per_day=default_articles_per_day)
    return SeoEditorialPreferences(
        timezone_name=row.timezone_name or "Europe/Moscow",
        autorun_interval_days=row.autorun_interval_days or default_interval_days,
        autorun_horizon_days=row.autorun_horizon_days or 7,
        publications_per_day=row.publications_per_day or default_publications_per_day,
        articles_per_day=row.articles_per_day or default_articles_per_day,
    )


def set_editorial_preferences(db: Session, product_id: UUID, *,
                             timezone_name: str, autorun_interval_days: int,
                             autorun_horizon_days: int, publications_per_day: int,
                             articles_per_day: int, actor: str) -> SeoEditorialPreferences:
    """Owner-only settings mutation; never activates SEO implicitly.

    All producer paths serialize on the same product row to avoid races with
    opt-out and calendar writes.
    """
    if not isinstance(timezone_name, str) or not timezone_name or len(timezone_name) > 64:
        raise ValueError("Укажите корректный часовой пояс")
    try:
        ZoneInfo(timezone_name)
    except (ValueError, ZoneInfoNotFoundError, KeyError) as exc:
        raise ValueError("Неизвестный часовой пояс IANA") from exc
    if isinstance(autorun_interval_days, bool) or not isinstance(autorun_interval_days, int) or not 1 <= autorun_interval_days <= 90:
        raise ValueError("Интервал SEO-плана должен быть 1–90 дней")
    if autorun_horizon_days not in (7, 30):
        raise ValueError("План составляется на 7 или 30 дней")
    for name, value, maximum in (("публикаций", publications_per_day, 30),
                                 ("генераций", articles_per_day, 300)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"Количество {name} должно быть от 1 до {maximum} в сутки")
    locked = db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
    if locked is None:
        raise LookupError("Компания не найдена")
    row = db.get(SeoFactoryActivation, product_id)
    if row is None:
        row = SeoFactoryActivation(product_id=product_id, enabled=False,
                                   changed_at=datetime.now(timezone.utc), changed_by=actor[:200])
        db.add(row)
    row.timezone_name = timezone_name
    row.autorun_interval_days = autorun_interval_days
    row.autorun_horizon_days = autorun_horizon_days
    row.publications_per_day = publications_per_day
    row.articles_per_day = articles_per_day
    row.changed_at = datetime.now(timezone.utc)
    row.changed_by = actor[:200]
    db.commit()
    return editorial_preferences(db, product_id)
