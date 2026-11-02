"""Conservative, opt-in SEO factory cadence for ServGo's own marketing profile.

This scheduler only *queues* an existing factory job; it never bypasses the
existing critic, publication, opt-in or per-company article limits. The flag
defaults to OFF until a public HTTPS/robots/sitemap/analytics release check.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus
from app.seo.activation import is_enabled
from app.seo.factory import AGENT, queue_factory
from app.seo.queue import SEO_TASK_PREFIX


def _utc(value: datetime) -> datetime:
    """SQLite may return naive timestamps; the application stores UTC."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def enqueue_servgo_if_due(db: Session, settings, *, now: datetime | None = None) -> bool:
    """Queue at most one bounded ServGo SEO run after a quiet interval.

    The product row lock serializes this check with manual/dispatcher starts in
    PostgreSQL. It cannot start a run while existing SEO articles are still
    being processed or waiting for owner/editorial review.
    """
    if not settings.seo_servgo_autorun_enabled:
        return False
    now = _utc(now or datetime.now(timezone.utc))

    product = db.scalar(select(Product).where(Product.slug == "servgo").with_for_update())
    if product is None or not is_enabled(db, product.id):
        return False

    unfinished = db.scalar(select(Task.id).where(
        Task.product_id == product.id,
        Task.title.like(f"{SEO_TASK_PREFIX}%"),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL]),
    ).limit(1))
    if unfinished is not None:
        return False

    unresolved_article = db.scalar(select(Artifact.id)
        .join(Task, Task.id == Artifact.task_id)
        .where(
            Task.product_id == product.id,
            Task.title.like(f"{SEO_TASK_PREFIX}%"),
            Artifact.kind == "article",
            Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
        ).limit(1))
    if unresolved_article is not None:
        return False

    recent = list(db.scalars(select(Task).where(
        Task.product_id == product.id, Task.assigned_agent == AGENT,
    ).order_by(Task.created_at.desc()).limit(settings.seo_servgo_autorun_max_runs_28d + 1)))
    if any(t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING) for t in recent):
        return False
    if recent and _utc(recent[0].created_at) > now - timedelta(days=settings.seo_servgo_autorun_interval_days):
        return False
    if sum(_utc(task.created_at) >= now - timedelta(days=28) for task in recent) >= settings.seo_servgo_autorun_max_runs_28d:
        return False

    queue_factory(
        db, product.id,
        focus=("Органический трафик для продаж ServGo: практические вопросы владельцев "
               "сервисных организаций о записи клиентов, расписании, автоматизации, "
               "предоплате, маркетинге и сравнении подходов. Без неподтверждённых обещаний."),
    )
    return True
