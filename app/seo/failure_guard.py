"""Tenant-scoped SEO automatic-failure circuit breaker (not a billing limit).

Protects other queued articles from repeated *same-class technical* failures.
A human's explicit manual retry of a FAILED SEO article is exempt, not all
articles from that tenant. Never treat unknown monetary estimates as real spend.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact, Task, TaskStatus
from app.seo.schedule import db_time

SEO_PREFIX = "SEO-ARTICLE:"
FAILURE_WINDOW_MINUTES = 30
TECHNICAL_FAILURE_THRESHOLD = 5

TECHNICAL_MARKERS = (
    "assembly_failed:", "invalid_article_output:", "provider_request_failed:",
    "context_preparation_failed:", "provider_transient", "TimeoutError",
    "ReadTimeout", "ProviderEmptyResponseError", "ProviderMalformedResponseError",
)


def technical_failure_class(error: str | None) -> str | None:
    """Only known technical error codes; ignore editorial/critic rejections."""
    if not error:
        return None
    value = error.removeprefix("upstream_failed:")
    for marker in TECHNICAL_MARKERS:
        if marker in value:
            return marker.rstrip(":")
    return None


def _recent_failed_tasks(db: Session, now: datetime):
    cutoff = db_time(db, now - timedelta(minutes=FAILURE_WINDOW_MINUTES))
    tasks = list(db.scalars(select(Task).where(
        Task.assigned_agent == "content_factory",
        Task.title.like(f"{SEO_PREFIX}%"),
        Task.status == TaskStatus.FAILED,
        Task.updated_at >= cutoff,
    )))
    if not tasks:
        return []
    errors = list(db.execute(select(AgentRun.task_id, AgentRun.error, AgentRun.finished_at)
        .where(AgentRun.task_id.in_([t.id for t in tasks]),
               AgentRun.error.is_not(None))
        .order_by(AgentRun.finished_at.desc(), AgentRun.id.desc())))
    last = {}
    for task_id, error, _ in errors:
        last.setdefault(task_id, error)
    return [(task, technical_failure_class(last.get(task.id))) for task in tasks]


def paused_products(db: Session, now: datetime | None = None) -> set[UUID]:
    """Pause ONLY if several different articles fail with the SAME technical code."""
    now = now or datetime.now(timezone.utc)
    grouped: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
    for task, kind in _recent_failed_tasks(db, now):
        if kind:
            grouped[(task.product_id, kind)].add(task.id)
    return {company for (company, _), task_ids in grouped.items()
            if len(task_ids) >= TECHNICAL_FAILURE_THRESHOLD}


def owner_authorized_task_ids(db: Session, product_ids: set[UUID]) -> set[UUID]:
    """Manual retries from the existing authenticated SEO owner form."""
    if not product_ids:
        return set()
    return set(db.scalars(select(Artifact.task_id).where(
        Artifact.product_id.in_(product_ids),
        Artifact.kind == "seo_article_manual_retry",
        Artifact.task_id.is_not(None),
    )))


def automatic_generation_paused(db: Session, *, product_id: UUID,
                                task_id: UUID, now: datetime | None = None) -> bool:
    if product_id not in paused_products(db, now):
        return False
    return task_id not in owner_authorized_task_ids(db, {product_id})
