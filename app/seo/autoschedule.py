"""Optional weekly SEO planning only for ServGo's own profile."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Product, Task, TaskStatus
from app.seo.activation import is_enabled
from app.seo.factory import AGENT, queue_factory


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def enqueue_servgo_if_due(db: Session, settings, *, now: datetime | None = None) -> bool:
    """Start a weekly plan if due; do not impose a monthly cap or article backlog gate.

    The factory already prevents simultaneous active runs per business.
    A manually requested 30-day plan counts as the most recent run.
    """
    if not settings.seo_servgo_autorun_enabled:
        return False
    now = _utc(now or datetime.now(timezone.utc))
    product = db.scalar(select(Product).where(Product.slug == "servgo").with_for_update())
    if product is None or not is_enabled(db, product.id):
        return False
    latest = db.scalar(select(Task.created_at).where(
        Task.product_id == product.id, Task.assigned_agent == AGENT,
    ).order_by(Task.created_at.desc()).limit(1))
    if latest is not None and _utc(latest) > now - timedelta(days=settings.seo_servgo_autorun_interval_days):
        return False
    existing = db.scalar(select(Task.id).where(
        Task.product_id == product.id, Task.assigned_agent == AGENT,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
    ).limit(1))
    if existing is not None:
        return False
    queue_factory(
        db, product.id,
        focus=("Органический трафик для продаж ServGo: вопросы владельцев сервисных "
               "организаций об онлайн-записи, расписании, автоматизации, предоплате, "
               "маркетинге и сравнении решений, без неподтверждённых обещаний."),
        horizon_days=7,
    )
    return True
