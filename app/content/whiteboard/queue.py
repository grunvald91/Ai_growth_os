"""Approval-gated jobs stored in existing Growth OS Task and Artifact tables.

No API route or worker is registered yet: these requests cannot spend money.
"""
import hashlib
from uuid import UUID

from sqlalchemy import select

from app.content.whiteboard.contracts import WhiteboardRequest
from app.models.domain import Product, TaskStatus
from app.services.approvals import ApprovalService
from app.services.artifacts import ArtifactService
from app.services.tasks import TaskService


def queue_whiteboard(db, request: WhiteboardRequest):
    product = db.scalar(select(Product).where(Product.id == request.product_id).with_for_update())
    if product is None:
        raise LookupError("Product not found")
    # Exact content dedupe only; do not include provider secrets or arbitrary file paths.
    data = request.model_dump_json()
    fingerprint = hashlib.sha256(data.encode("utf-8")).hexdigest()[:20]
    title = "WHITEBOARD: " + fingerprint
    existing = TaskService(db).find_active_duplicate(
        product_id=product.id, title=title, assigned_agent="whiteboard_renderer",
    )
    if existing:
        return existing
    task = TaskService(db).create(
        product_id=product.id, title=title, assigned_agent="whiteboard_renderer",
        status=TaskStatus.WAITING_APPROVAL, priority=50,
        description="Whiteboard explainer preparation; paid provider calls require owner approval.",
    )
    artifact = ArtifactService(db).create(
        product_id=product.id, task_id=task.id, kind="whiteboard_request",
        title="Рисованное видео: " + request.topic[:200],
        content=data, status="awaiting_approval",
    )
    ApprovalService(db).create(
        product_id=product.id, artifact_id=artifact.id,
        action_type="paid_whiteboard_video_generation", risk_level="YELLOW",
        status="pending", requested_by="owner",
        reason="Confirm providers and maximum API spend before any generation.",
    )
    db.commit()
    return task
