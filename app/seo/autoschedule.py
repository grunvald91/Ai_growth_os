"""Opt-in weekly SEO planning for any enabled company, with a legacy ServGo pilot.

The operator-wide switch defaults OFF until end-to-end acceptance. Product
activation remains independently required: no demo or unrelated company is
silently opted in. One task per scheduler tick bounds model spend.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Product, Task, TaskStatus
from app.seo.activation import SeoFactoryActivation, is_enabled
from app.seo.factory import AGENT, queue_factory


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _queue_locked_if_due(db: Session, product_id, *, now: datetime, interval_days: int,
                         focus: str | None = None) -> bool:
    """Call only while holding this company's Product row lock."""
    if not is_enabled(db, product_id):
        return False
    latest = db.scalar(select(Task.created_at).where(
        Task.product_id == product_id, Task.assigned_agent == AGENT,
    ).order_by(Task.created_at.desc()).limit(1))
    if latest is not None and _utc(latest) > now - timedelta(days=interval_days):
        return False
    active = db.scalar(select(Task.id).where(
        Task.product_id == product_id, Task.assigned_agent == AGENT,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
    ).limit(1))
    if active is not None:
        return False
    queue_factory(db, product_id, focus=focus, horizon_days=7)
    return True


def enqueue_servgo_if_due(db: Session, settings, *, now: datetime | None = None) -> bool:
    """Compatibility with the existing opt-in ServGo-only pilot.

    A manually requested 30-day plan counts towards the same weekly interval.
    """
    if not settings.seo_servgo_autorun_enabled:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    product = db.scalar(select(Product.id).where(Product.slug == "servgo").with_for_update())
    if product is None:
        return False
    return _queue_locked_if_due(
        db, product, now=now, interval_days=settings.seo_servgo_autorun_interval_days,
        focus=("Органический трафик для продаж ServGo: вопросы владельцев сервисных "
               "организаций об онлайн-записи, расписании, автоматизации, предоплате, "
               "маркетинге и сравнении решений, без неподтверждённых обещаний."),
    )


def enqueue_enabled_if_due(db: Session, settings, *, now: datetime | None = None) -> bool:
    """Queue one due weekly plan across ALL authorized, SEO-enabled companies.

    The global feature flag defaults OFF pending production acceptance. When
    OFF, the old explicit ServGo-only pilot flag continues to work unchanged.
    Query order is deterministic, all producer paths use the same product
    lock, and the factory deduplicates a simultaneously active run.
    """
    if not settings.seo_autorun_enabled:
        return enqueue_servgo_if_due(db, settings, now=now)
    now = _utc(now or datetime.now(timezone.utc))
    enabled = list(db.scalars(select(SeoFactoryActivation.product_id).where(
        SeoFactoryActivation.enabled.is_(True),
    ).order_by(SeoFactoryActivation.changed_at, SeoFactoryActivation.product_id)))
    for product_id in enabled:
        # This lock serializes the due check with the Studio manual run and
        # the owner's opt-out. No other tenant can alter this product's work.
        locked = db.scalar(select(Product.id).where(Product.id == product_id).with_for_update())
        if locked is None:
            continue
        if _queue_locked_if_due(
            db, locked, now=now, interval_days=settings.seo_autorun_interval_days,
        ):
            return True
    return False
