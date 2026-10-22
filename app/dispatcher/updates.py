"""Results coming back into the conversation: a task started from the dispatcher finishes, and the dispatcher
says so with the result in hand (a card to approve, a plan to approve, or a link to the failure).

Each finished task is reported once per outcome. Reading never changes anything except adding that message.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.dispatcher.materials import KIND_LABELS, card
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.dispatcher.outcome import Context
from app.models.domain import Artifact, ArtifactStatus, Task, TaskStatus

RESULT_CAPABILITY = "task_result"
RESULT_KINDS = ("channel_text", "article", "creative_brief", "video_script", "video_render", "whiteboard_video",
                "editorial_plan")
READY = (ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.DRAFT)


def _task_block(message: DispatcherMessage, task: Task) -> dict | None:
    return next((b for b in message.blocks if b.get("type") == "task" and b.get("task_id") == str(task.id)), None)


def _result(ctx: Context, task: Task) -> Artifact | None:
    return ctx.db.scalar(select(Artifact).where(
        Artifact.product_id == ctx.product.id, Artifact.task_id == task.id, Artifact.kind.in_(RESULT_KINDS),
        Artifact.status.in_(READY)).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))


def _completed(ctx: Context, task: Task, title: str, href: str) -> tuple[str, list[dict]]:
    base = f"/studio/products/{ctx.product.id}"
    result = _result(ctx, task)
    if result is None:
        return f"Готово: {title}.", [{"type": "links", "items": [{"label": "Открыть", "href": href}]}]
    if result.kind == "editorial_plan":
        return "Контент-план готов. Он ничего не публикует, пока вы его не утвердите.", [
            {"type": "chips", "items": [{"label": "Утвердить контент-план", "capability": "approve_plan",
                                         "params": {}}]},
            {"type": "links", "items": [{"label": "Открыть план", "href": f"{base}/plan?view=draft"}]}]
    item = card(ctx, result)
    if result.kind == "whiteboard_video":
        item = {**item, "kind": "Рисованное видео", "href": f"{base}/whiteboard/{task.id}"}
    blocks: list[dict] = [item]
    if result.status == ArtifactStatus.AWAITING_APPROVAL and result.kind in KIND_LABELS:
        blocks.append({"type": "chips", "items": [{"label": "Утвердить", "capability": "approve_material",
                                                   "params": {"material_id": str(result.id)}}]})
    text = f"Готово: {title}." + (" Чтобы доработать, напишите, что поправить."
                                  if result.kind == "channel_text" else "")
    return text, blocks


def collect_updates(db: Session, ctx: Context, conversation: DispatcherConversation) -> list[DispatcherMessage]:
    """Add one message per newly finished task of this conversation and return the new messages."""
    from app.dispatcher.service import _add_committed
    db.scalar(select(DispatcherConversation).where(DispatcherConversation.id == conversation.id).with_for_update())
    reported = {(row.task_id, (row.params or {}).get("status")) for row in db.scalars(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.capability == RESULT_CAPABILITY))}
    pending = db.execute(select(DispatcherMessage, Task).join(Task, Task.id == DispatcherMessage.task_id).where(
        DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.role == "assistant",
        or_(DispatcherMessage.capability.is_(None), DispatcherMessage.capability != RESULT_CAPABILITY),
        Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
    ).order_by(DispatcherMessage.seq)).all()
    added: list[DispatcherMessage] = []
    seen: set = set()
    for message, task in pending:
        status = task.status.value
        key = (task.id, status)
        if key in reported or key in seen:
            continue
        seen.add(key)
        block = _task_block(message, task)
        title = (block or {}).get("title") or task.title.split(": ", 1)[-1]
        href = (block or {}).get("href") or f"/studio/products/{ctx.product.id}/tasks/{task.id}"
        if status == "completed":
            text, blocks = _completed(ctx, task, title, href)
        else:
            text = f"Не получилось: {title}. Подробности на странице задания."
            blocks = [{"type": "links", "items": [{"label": "Открыть задание", "href": href}]}]
        from app.dispatcher.blocks import clean_blocks
        added.append(_add_committed(
            db, conversation, role="assistant", input_mode="text", text=text, spoken=text,
            blocks=clean_blocks([{"type": "text", "text": text}, *blocks]), intent="answer",
            capability=RESULT_CAPABILITY, params={"status": status}, task_id=task.id))
    return added
