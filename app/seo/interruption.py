"""Recover only SEO claims with a provable, released process lease.

The API and worker share products_root. A nonblocking flock is held for the
entire workflow, including inference. A persistent claim marker distinguishes
new protected tasks from historical RUNNING rows without a lease.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
from pathlib import Path

from sqlalchemy import select, exists, func

from app.models.domain import AgentRun, Artifact, Product, Task, TaskStatus
from app.seo.activation import is_enabled
from app.seo.schedule import db_time

LEASE = "seo_article_process_lease"
RECOVERY = "seo_article_interruption_recovery"
GRACE_SECONDS = 90
MAX_RECOVERIES = 2


class ArticleLeaseBusy(RuntimeError):
    pass


@contextmanager
def article_lease(root, product_id, task_id):
    directory = Path(root) / ".seo-article-leases" / str(product_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / (str(task_id) + ".lock")).open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ArticleLeaseBusy("SEO article claim still owned") from None
        yield


def record_lease(db, task):
    db.add(Artifact(product_id=task.product_id, task_id=task.id,
        kind=LEASE, title="SEO article process lease", content="{}"))


def recover_interrupted_articles(session_factory, products_root, *, now=None, limit=20):
    now = now or datetime.now(timezone.utc)
    with session_factory() as db:
        cutoff = db_time(db, now - timedelta(seconds=GRACE_SECONDS))
        candidates = list(db.execute(select(Task.product_id, Task.id).where(
            Task.status == TaskStatus.RUNNING, Task.assigned_agent == "content_factory",
            Task.title.like("SEO-ARTICLE:%"), Task.updated_at <= cutoff,
            exists(select(Artifact.id).where(Artifact.product_id == Task.product_id,
                Artifact.task_id == Task.id, Artifact.kind == LEASE)),
        ).order_by(Task.updated_at, Task.id).limit(limit)))
    recovered = 0
    for product_id, task_id in candidates:
        try:
            with article_lease(products_root, product_id, task_id):
                with session_factory() as db:
                    if db.scalar(select(Product.id).where(
                            Product.id == product_id).with_for_update()) is None:
                        continue
                    task = db.scalar(select(Task).where(
                        Task.id == task_id, Task.product_id == product_id,
                        Task.status == TaskStatus.RUNNING,
                        Task.assigned_agent == "content_factory",
                        Task.title.like("SEO-ARTICLE:%"),
                        Task.updated_at <= db_time(db, now - timedelta(seconds=GRACE_SECONDS)),
                    ).with_for_update())
                    if task is None:
                        continue
                    if not db.scalar(select(exists().where(
                        Artifact.product_id == product_id, Artifact.task_id == task_id,
                        Artifact.kind == LEASE))):
                        continue
                    article_exists = db.scalar(select(exists().where(
                        Artifact.product_id == product_id, Artifact.task_id == task_id,
                        Artifact.kind == "article")))
                    previous = db.scalar(select(func.count()).select_from(Artifact).where(
                        Artifact.product_id == product_id, Artifact.task_id == task_id,
                        Artifact.kind == RECOVERY)) or 0
                    if not is_enabled(db, product_id):
                        status, reason = TaskStatus.CANCELLED, "seo_disabled"
                    elif article_exists:
                        status, reason = TaskStatus.FAILED, "existing_article_reconciliation_required"
                    elif previous >= MAX_RECOVERIES:
                        status, reason = TaskStatus.FAILED, "interruption_limit"
                    else:
                        status, reason = TaskStatus.QUEUED, "process_lease_released"
                    for run in db.scalars(select(AgentRun).where(
                            AgentRun.task_id == task_id,
                            AgentRun.status.in_(["running", "queued"]))):
                        run.status, run.error = "failed", "seo_article_interrupted"
                        run.finished_at = now
                    task.status = status
                    db.add(Artifact(product_id=product_id, task_id=task_id, kind=RECOVERY,
                        title="SEO interrupted article recovery", content=json.dumps({
                            "reason": reason, "status": status.value, "at": now.isoformat(),
                        })))
                    db.commit()
                    recovered += 1
        except ArticleLeaseBusy:
            continue
    return recovered
