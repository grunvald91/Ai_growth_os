"""Owner decisions on ready materials, shared by the Studio pages and the dispatcher.

One implementation means the dispatcher cannot approve anything the page would refuse.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.render import asset_path
from app.content.telegram_publishing import validate_telegram_text
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus

REVIEW_KINDS = ("channel_text", "creative_brief", "article", "video_script", "video_render")


class ReviewError(Exception):
    """A refusal with an HTTP-like status and a message the owner can read."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def material_data(row: Artifact) -> dict:
    try:
        value = json.loads(row.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def needs_fact_check(row: Artifact) -> bool:
    return bool(material_data(row).get("fact_check_required"))


def approve_material(db: Session, products_root: Path, product_id: UUID, artifact_id: UUID, *,
                     facts_confirmed: bool = False) -> Artifact:
    """Approve a material that awaits the owner. Already approved is returned unchanged."""
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    row = db.scalar(select(Artifact).where(
        Artifact.id == artifact_id, Artifact.product_id == product_id, Artifact.kind.in_(REVIEW_KINDS),
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise ReviewError(404, "Материал не найден")
    if row.status == ArtifactStatus.APPROVED:
        return row
    if row.status != ArtifactStatus.AWAITING_APPROVAL:
        raise ReviewError(409, "Согласовать эту версию уже нельзя")
    if needs_fact_check(row) and not facts_confirmed:
        raise ReviewError(422, "Сначала проверьте числа, цены и результаты в полном просмотре материала")
    if row.kind == "video_render":
        metadata = material_data(row)
        try:
            script_id = UUID(str(metadata["script_id"]))
            video_file = asset_path(products_root, product_id, script_id, "video.mp4")
        except (ValueError, KeyError, TypeError):
            raise ReviewError(409, "У видео нет корректных исходных данных") from None
        if not video_file.is_file():
            raise ReviewError(409, "Видеофайл ещё не создан или недоступен")
    if row.kind == "channel_text":
        source = material_data(row)
        if not source.get("body"):
            raise ReviewError(409, "Текст материала отсутствует")
        if source.get("channel") == "telegram":
            try:
                validate_telegram_text(source["body"])
            except ValueError as exc:
                raise ReviewError(422, str(exc)) from None
        pending = db.scalar(select(Task.id).where(
            Task.product_id == product_id, Task.assigned_agent == "content_editor",
            Task.title == "EDIT: " + str(row.id), Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]),
        ).limit(1))
        if pending:
            raise ReviewError(409, "Доработка ещё выполняется — подтвердите новую версию")
    row.status = ArtifactStatus.APPROVED
    row.version += 1
    db.commit()
    return row


def schedule_owner_post(db: Session, product_id: UUID, post_id: UUID, connection_id: UUID, when):
    """Schedule an approved Telegram post. If it belongs to a slot of the active plan at exactly this time,
    the slot is linked to the publication, as on the channels page. Raises ValueError / LookupError with a
    message the owner can read."""
    from app.api.content_studio import latest_plan, parse_dt  # local: that module imports the whole Studio
    from app.content.telegram_publishing import schedule_post
    post = db.get(Artifact, post_id)
    if post is None or post.product_id != product_id:
        raise LookupError("Пост другой компании")
    plan_id, slot_id = None, None
    plan_row, plan = latest_plan(db, product_id)
    if plan_row and plan and plan.get("approval_state") == "active":
        matches = [slot for slot in plan.get("slots", []) if
                   slot.get("task_id") == str(post.task_id) and slot.get("platform") == "telegram"
                   and not slot.get("telegram_publication_id") and not slot.get("publication_url")
                   and parse_dt(slot.get("publish_at")) == when]
        if len(matches) == 1:
            plan_id, slot_id = plan_row.id, str(matches[0]["slot_id"])
    return schedule_post(db, post_id, connection_id, when, plan_id=plan_id, slot_id=slot_id)
