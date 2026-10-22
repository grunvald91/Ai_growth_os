"""Read-only views of the company state, built by code.

Numbers and lists shown to the owner come from here, never from the model's text.
Imports of the Studio pages are local: those modules import a lot and must not load with this one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Product, Task, TaskStatus

ACTIVE = {"queued", "running", "waiting_approval"}


def attention(db: Session, product_id: UUID) -> list[dict]:
    """What needs the owner now: materials to review, a plan draft, a video to confirm, failures."""
    from app.api.content_studio_activity import notice_items
    return notice_items(db, product_id, limit=50)


def work_rows(db: Session, product_id: UUID) -> list[dict]:
    from app.api.content_studio_activity import work_items
    return work_items(db, product_id, limit=60)


def plans(db: Session, product_id: UUID):
    """(active plan row and data or None, draft row and data or None)."""
    from app.api.content_studio import latest_draft, latest_plan, reconcile_plan_states
    row, plan = latest_plan(db, product_id)
    active = None
    if row is not None and plan and plan.get("approval_state") == "active":
        active = (row, reconcile_plan_states(db, product_id, plan))
    draft_row, draft = latest_draft(db, product_id)
    return active, ((draft_row, draft) if draft_row is not None else None)


def upcoming_slots(plan: dict, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    result = []
    for slot in plan.get("slots") or []:
        try:
            when = datetime.fromisoformat(str(slot.get("publish_at")))
        except ValueError:
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if when >= now and slot.get("state") not in {"published", "cancelled", "missed"}:
            result.append({**slot, "_when": when})
    result.sort(key=lambda item: item["_when"])
    return result


def snapshot(db: Session, product: Product) -> dict:
    """Compact company state for the model. Counts only, no client data."""
    rows = work_rows(db, product.id)
    notes = attention(db, product.id)
    active, draft = plans(db, product.id)
    return {
        "company": product.name,
        "in_work": sum(1 for row in rows if row["state"] in ACTIVE),
        "awaiting_owner": sum(1 for note in notes if note["kind"] == "review"),
        "failed_recently": sum(1 for note in notes if note["kind"] == "error"),
        "plan": ("есть утверждённый" if active else "нет утверждённого") + (", есть черновик на утверждение" if draft else ""),
    }


def latest_active_video(db: Session, product_id: UUID) -> Task | None:
    return db.scalar(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "whiteboard_renderer",
        Task.status.in_([TaskStatus.WAITING_APPROVAL, TaskStatus.QUEUED, TaskStatus.RUNNING]),
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(1))
