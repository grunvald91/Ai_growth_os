"""Fact-based, read-only morning briefing for one Growth organisation.

A completed task is not a published post; missing external analytics are never
converted to zeroes or to claims about leads. All queries are product-scoped.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.content_studio_activity import RESULT_KINDS, notice_items
from app.dispatcher.readers import plans, upcoming_slots
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus


def home_summary(db: Session, product: Product) -> dict:
    now = datetime.now(timezone.utc)
    product_id = product.id
    active = db.scalar(
        select(func.count(Task.id)).where(
            Task.product_id == product_id,
            Task.status.in_((TaskStatus.QUEUED, TaskStatus.RUNNING)),
        )
    ) or 0
    waiting_video = db.scalar(
        select(func.count(Task.id)).where(
            Task.product_id == product_id,
            Task.assigned_agent == "whiteboard_renderer",
            Task.status == TaskStatus.WAITING_APPROVAL,
        )
    ) or 0
    awaiting_artifacts = db.scalar(
        select(func.count(Artifact.id)).where(
            Artifact.product_id == product_id,
            Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
            Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
        )
    ) or 0
    # Recent drafts are visible in the existing review feed, not auto-approved.
    recent_drafts = db.scalar(
        select(func.count(Artifact.id)).where(
            Artifact.product_id == product_id,
            Artifact.status == ArtifactStatus.DRAFT,
            Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
            Artifact.created_at >= now - timedelta(days=3),
        )
    ) or 0
    approved_plan, draft_plan = plans(db, product_id)
    upcoming = upcoming_slots(approved_plan[1], now) if approved_plan else []
    upcoming_week = [slot for slot in upcoming if slot["_when"] < now + timedelta(days=7)]
    notices = notice_items(db, product_id, limit=50)
    attention = [
        {"title": note["text"], "kind": note["kind"], "url": note["url"]}
        for note in notices[:5]
    ]
    upcoming_cards = [
        {
            "topic": str(slot.get("topic") or "Публикация")[:120],
            "platform": str(slot.get("platform") or "")[:50],
            "when": slot["_when"].isoformat(),
            "state": str(slot.get("state") or "planned")[:40],
        }
        for slot in upcoming[:4]
    ]
    return {
        "company": product.name,
        "in_work": active,
        "review": awaiting_artifacts + recent_drafts + waiting_video + (1 if draft_plan else 0),
        "planned_week": len(upcoming_week),
        "attention": attention,
        "upcoming": upcoming_cards,
        "has_active_plan": approved_plan is not None,
        "has_draft_plan": draft_plan is not None,
        "generated_at": now.isoformat(),
    }
