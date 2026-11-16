"""Read-only views of the company state, built by code.

Numbers and lists shown to the owner come from here, never from the model's text.
Imports of the Studio pages are local: those modules import a lot and must not load with this one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

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
    # Bounded, company-scoped identifiers prevent hallucinated plan targets.
    editable: dict = {"draft_packages": [], "calendar_slots": []}
    if draft is not None:
        _, draft_data = draft
        unique = {}
        for slot in draft_data.get("slots") or []:
            key = slot.get("package_key")
            if key and key not in unique:
                unique[key] = {"package_key": str(key), "topic": str(slot.get("topic") or "")[:180]}
        editable["draft_packages"] = list(unique.values())[:30]
    calendar_source = active or draft
    if calendar_source is not None:
        _, data = calendar_source
        editable["timezone"] = str(data.get("timezone") or "Europe/Moscow")
        editable["calendar_plan_state"] = str(data.get("approval_state") or "")
        editable["calendar_slots"] = [
            {"slot_id": str(s["slot_id"]), "platform": str(s.get("platform") or ""),
             "topic": str(s.get("topic") or "")[:100],
             "publish_at": str(s.get("publish_at") or "")}
            for s in upcoming_slots(data) if s.get("slot_id")
        ][:20]
    return {
        "editable_plan": editable,
        "company": product.name,
        "today": datetime.now(ZoneInfo("Europe/Moscow")).strftime("%Y-%m-%d, %A, %H:%M (Москва)"),
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
