"""Hands the business facts to the marketing team (the CMO review) from the existing task queue.

The facts were already stored as metrics when the owner asked. This worker only runs the review over them and leaves
a result for the dispatcher to report. It starts nothing outward: the review itself publishes nothing and spends no
advertising money.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update

from app.models.domain import Artifact, ArtifactStatus, Task, TaskStatus

AGENT = "business_insights"
RESULT_KIND = "business_review"
STALE_MINUTES = 25
_logger = logging.getLogger(__name__)

FOCUS = (
    "The metrics whose source starts with servgo_schedule are facts about the bookings of the owner's venue over the "
    "chosen period, computed by the system from the booking platform. Treat them as the main evidence. Find the 3-5 "
    "most useful findings that can bring the owner more clients (weak days and times to fill, how to bring clients "
    "back, which services to push, what to test). Each decision: plain Russian for a venue owner, concrete numbers "
    "taken from the metrics, and evidence_references of kind metric pointing at the metrics it relies on. "
    "If the data is thin, say so and propose a TEST instead of a confident conclusion. "
    "Never invent numbers, reasons or clients. The text is read by the owner: never mention metric names, source "
    "names, IDs, data sets or the company's technical name; speak about bookings, clients, days and money."
)


def focus_for(depth_days: int) -> str:
    """The focus names the exact set handed over, so an older set with another depth is not mixed in."""
    return FOCUS + f" Use the set with source servgo_schedule:{depth_days}d; ignore other servgo_schedule sets."


def queue(db, product_id: UUID, depth_days: int, metric_count: int, *, commit: bool = True) -> tuple[Task, bool]:
    """One active hand-over per company: asking twice returns the one already waiting or running."""
    existing = db.scalar(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == AGENT,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])).limit(1))
    if existing is not None:
        return existing, False
    task = Task(product_id=product_id, assigned_agent=AGENT, title="Маркетинг: разбор данных бизнеса",
                description=f"Цифры записей за {depth_days} дн. ({metric_count} показателей) переданы команде для разбора. "
                            f"Набор: servgo_schedule:{depth_days}d.",
                priority=100, status=TaskStatus.QUEUED)
    db.add(task)
    db.flush()
    if commit:
        db.commit()
    return task, True


def _fail_stale(db) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_MINUTES)
    for task in db.scalars(select(Task).where(Task.assigned_agent == AGENT, Task.status == TaskStatus.RUNNING,
                                             Task.updated_at < cutoff).limit(10)):
        task.status = TaskStatus.FAILED
    db.commit()


async def run_insights_once(session_factory, settings, *, builder=None) -> int:
    from app.core.composition import build_review
    with session_factory() as db:
        _fail_stale(db)
        task_id = db.scalar(select(Task.id).where(Task.assigned_agent == AGENT, Task.status == TaskStatus.QUEUED)
                            .order_by(Task.created_at, Task.id).limit(1))
        if task_id is None:
            return 0
        claimed = db.execute(update(Task).where(Task.id == task_id, Task.status == TaskStatus.QUEUED)
                             .values(status=TaskStatus.RUNNING))
        if claimed.rowcount != 1:
            db.rollback()
            return 0
        db.commit()
        task = db.get(Task, task_id)
        product_id = task.product_id
        found = re.search(r"servgo_schedule:(\d+)d", task.description or "")
        focus = focus_for(int(found.group(1))) if found else FOCUS
    with session_factory() as db:  # the review owns its commits and needs a clean session
        try:
            result = await (builder or build_review)(db, settings).run(product_id, focus=focus)
        except Exception as exc:  # noqa: BLE001 - the worker must survive provider and configuration failures
            db.rollback()
            _logger.warning("business_review_failed %s", type(exc).__name__)
            result = None
        task = db.get(Task, task_id)
        if result is None or result.status != "completed":
            task.status = TaskStatus.FAILED
            db.commit()
            return 1
        summary = ""
        for artifact_id in result.artifact_ids:
            artifact = db.get(Artifact, artifact_id)
            if artifact is not None and artifact.kind == "growth_review":
                try:
                    summary = str(json.loads(artifact.content).get("summary", ""))
                except ValueError:
                    summary = ""
        db.add(Artifact(product_id=product_id, task_id=task_id, kind=RESULT_KIND, title="Разбор данных бизнеса",
                        content=json.dumps({"summary": summary, "review_task_id": str(result.task_id),
                                            "decision_ids": [str(i) for i in result.decision_ids],
                                            "task_ids": [str(i) for i in result.task_ids]}, ensure_ascii=False),
                        status=ArtifactStatus.DRAFT))
        task.status = TaskStatus.COMPLETED
        db.commit()
        return 1
