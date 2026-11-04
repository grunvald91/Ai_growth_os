"""Fact-based, read-only daily briefing for exactly one organisation.

Never confuse scheduled work with running work, completed drafts with
published articles, or unavailable external metrics with zero.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from app.api.content_studio_activity import RESULT_KINDS, notice_items
from app.dispatcher.readers import plans, upcoming_slots
from app.models.domain import Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.schedule import SEO_TASK_PREFIX


def _seo_schedule(db: Session, product_id, now: datetime) -> list[dict]:
    """Future SEO publication reservations, not claims of successful publishing."""
    published = exists(
        select(Publication.id).join(Artifact, Publication.artifact_id == Artifact.id).where(
            Publication.product_id == product_id,
            Publication.channel == "own_site",
            Publication.status == "published",
            Artifact.product_id == product_id,
            Artifact.task_id == Task.id,
        )
    ).correlate(Task)
    tasks = list(db.scalars(
        select(Task).where(
            Task.product_id == product_id,
            Task.assigned_agent == "content_factory",
            Task.title.like(f"{SEO_TASK_PREFIX}%"),
            Task.due_at.is_not(None),
            Task.due_at >= now,
            Task.status.in_((TaskStatus.QUEUED, TaskStatus.RUNNING,
                             TaskStatus.WAITING_APPROVAL, TaskStatus.COMPLETED)),
            ~published,
        ).order_by(Task.due_at, Task.id).limit(150)
    ))
    if not tasks:
        return []
    first = tasks[:8]
    content = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.task_id.in_([item.id for item in first]),
        Artifact.kind.in_(("content_request", "article")),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())).all()
    titles = {}
    for artifact in content:
        if artifact.kind == "article":
            titles[artifact.task_id] = str(artifact.title or "SEO-статья")[:120]
        elif artifact.task_id not in titles:
            try:
                brief = json.loads(artifact.content)
                topic = brief.get("topic") if isinstance(brief, dict) else None
            except (TypeError, ValueError):
                topic = None
            if isinstance(topic, str) and topic.strip():
                titles[artifact.task_id] = topic.strip()[:120]
    result = []
    for task in tasks:
        when = (task.due_at.replace(tzinfo=timezone.utc)
                if task.due_at.tzinfo is None else task.due_at.astimezone(timezone.utc))
        result.append({
            "topic": titles.get(task.id, "SEO-статья"),
            "platform": "Сайт · SEO-завод",
            "when": when.isoformat(),
            "state": "scheduled",
            "url": f"/studio/products/{product_id}/seo/queue",
        })
    return result


def home_summary(db: Session, product: Product) -> dict:
    now = datetime.now(timezone.utc)
    product_id = product.id
    base = f"/studio/products/{product_id}"
    active = db.scalar(select(func.count(Task.id)).where(
        Task.product_id == product_id,
        or_(
            Task.status == TaskStatus.RUNNING,
            and_(Task.status == TaskStatus.QUEUED,
                 or_(Task.due_at.is_(None), Task.due_at <= now)),
        ),
    )) or 0
    waiting_video = db.scalar(select(func.count(Task.id)).where(
        Task.product_id == product_id,
        Task.assigned_agent == "whiteboard_renderer",
        Task.status == TaskStatus.WAITING_APPROVAL,
    )) or 0
    awaiting_artifacts = db.scalar(select(func.count(Artifact.id)).where(
        Artifact.product_id == product_id,
        Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
        Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
    )) or 0
    recent_drafts = db.scalar(select(func.count(Artifact.id)).where(
        Artifact.product_id == product_id,
        Artifact.status == ArtifactStatus.DRAFT,
        Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
        Artifact.created_at >= now - timedelta(days=3),
    )) or 0

    approved_plan, draft_plan = plans(db, product_id)
    editorial_slots = upcoming_slots(approved_plan[1], now) if approved_plan else []
    upcoming = [{
        "topic": str(slot.get("topic") or "Публикация")[:120],
        "platform": str(slot.get("platform") or "")[:50],
        "when": slot["_when"].isoformat(),
        "state": str(slot.get("state") or "planned")[:40],
        "url": base + "/calendar",
    } for slot in editorial_slots]
    seo_slots = _seo_schedule(db, product_id, now)
    upcoming.extend(seo_slots)
    upcoming.sort(key=lambda row: row["when"])
    cutoff = now + timedelta(days=7)
    planned_week = sum(
        1 for row in upcoming if datetime.fromisoformat(row["when"]) < cutoff
    )
    seo_planned_week = sum(
        1 for row in seo_slots if datetime.fromisoformat(row["when"]) < cutoff
    )

    notices = notice_items(db, product_id, limit=50)
    seo_errors = list(db.scalars(select(Task).where(
        Task.product_id == product_id,
        Task.assigned_agent == "seo_factory",
        Task.status == TaskStatus.FAILED,
    ).order_by(Task.updated_at.desc(), Task.id.desc()).limit(2)))
    attention = [
        {"title": "SEO-завод: задание завершилось с ошибкой",
         "kind": "error", "url": base + "/seo/queue"}
        for error in seo_errors
    ]
    attention.extend(
        {"title": note["text"], "kind": note["kind"], "url": note["url"]}
        for note in notices
    )
    return {
        "company": product.name,
        "in_work": active,
        "review": awaiting_artifacts + recent_drafts + waiting_video + (1 if draft_plan else 0),
        "planned_week": planned_week,
        "seo_planned_week": seo_planned_week,
        "attention": attention[:5],
        "upcoming": upcoming[:4],
        "has_active_plan": bool(approved_plan or seo_slots),
        "has_editorial_plan": bool(approved_plan),
        "has_draft_plan": draft_plan is not None,
        "generated_at": now.isoformat(),
    }
