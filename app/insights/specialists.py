"""Specialists take the tasks the marketing director handed out after a business review.

The director already names a specialist for every task it proposes (researcher, content, distribution, analytics).
This worker lets that specialist do the task: one task per tick, a draft as the result. It publishes nothing, sends
nothing and starts no advertising: tasks that need an outward action or an approval are never taken here, and no
second review is started from their results.
"""
from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select

from app.agents import AgentRole
from app.insights.worker import RESULT_KIND
from app.models.domain import Artifact, Task, TaskStatus
from app.workflows.team_cycle import ROLE_TASKS, TASK_SCHEMAS

_logger = logging.getLogger(__name__)
REVIEWS_SCANNED = 100  # the newest reviews are enough: older ones have long been worked off


def _uuid(raw) -> UUID | None:
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _executable(db, task: Task) -> bool:
    """Same rules the team cycle applies, checked up front so a task it would skip never blocks the line."""
    brief = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "task_brief")
                      .order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))
    if brief is None:
        return False
    try:
        data = json.loads(brief.content)
    except (TypeError, ValueError):
        return False
    task_type = data.get("task_type")
    if task_type not in TASK_SCHEMAS:
        return False
    if task_type == "research":
        return True
    if data.get("operation") is not None or data.get("requires_approval") is True:
        return False
    try:
        role = AgentRole(task.assigned_agent)
    except (TypeError, ValueError):
        return False
    return task_type in ROLE_TASKS.get(role, frozenset())


def waiting_tasks(db) -> dict[UUID, set[UUID]]:
    """Queued tasks the specialists can do, grouped by company, among those that came from business reviews."""
    reviews = {}
    for result in db.scalars(select(Artifact).where(Artifact.kind == RESULT_KIND)
                             .order_by(Artifact.created_at.desc()).limit(REVIEWS_SCANNED)):
        try:
            review_task = _uuid(json.loads(result.content).get("review_task_id"))
        except ValueError:
            continue
        if review_task is not None:
            reviews[review_task] = result.product_id
    if not reviews:
        return {}
    found: dict[UUID, set[UUID]] = {}
    for task in db.scalars(select(Task).where(Task.parent_id.in_(list(reviews)), Task.status == TaskStatus.QUEUED)
                           .order_by(Task.priority.desc(), Task.created_at, Task.id)):
        if task.product_id == reviews.get(task.parent_id) and _executable(db, task):
            found.setdefault(task.product_id, set()).add(task.id)
    return found


async def run_specialists_once(session_factory, settings, *, builder=None) -> int:
    """Runs one specialist task; returns how many were run (0 when nothing waits)."""
    from app.core.composition import build_team_cycle
    with session_factory() as db:
        waiting = waiting_tasks(db)
    for product_id, task_ids in waiting.items():
        with session_factory() as db:
            try:
                result = await (builder or build_team_cycle)(db, settings).run(
                    product_id, max_tasks=1, followup_cmo=False, task_ids=task_ids)
            except Exception as exc:  # noqa: BLE001 - the worker must survive provider and configuration failures
                db.rollback()
                _logger.warning("specialist_task_failed %s", type(exc).__name__)
                continue
            for outcome in result.task_results:
                if outcome.status == "skipped":  # nothing ran; leaving it queued would hold up the tasks behind it
                    skipped = db.get(Task, outcome.task_id)
                    if skipped is not None and skipped.status == TaskStatus.QUEUED:
                        skipped.status = TaskStatus.FAILED
                        db.commit()
                    _logger.warning("specialist_task_skipped %s", outcome.error)
            if result.task_results:
                return len(result.task_results)
    return 0
