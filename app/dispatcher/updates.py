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
import json
from uuid import UUID

from app.models.domain import Artifact, ArtifactStatus, Decision, Task, TaskStatus

RESULT_CAPABILITY = "task_result"
RESULT_KINDS = ("channel_text", "article", "creative_brief", "video_script", "video_render", "whiteboard_video",
                "editorial_plan", "business_review")
READY = (ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.DRAFT)


def _task_block(message: DispatcherMessage, task: Task) -> dict | None:
    return next((b for b in message.blocks if b.get("type") == "task" and b.get("task_id") == str(task.id)), None)


def _result(ctx: Context, task: Task) -> Artifact | None:
    return ctx.db.scalar(select(Artifact).where(
        Artifact.product_id == ctx.product.id, Artifact.task_id == task.id, Artifact.kind.in_(RESULT_KINDS),
        Artifact.status.in_(READY)).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(1))


DECISION_LABELS = {"KEEP": "Оставить", "STOP": "Остановить", "SCALE": "Усилить", "TEST": "Проверить"}


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _business_review(ctx: Context, result: Artifact, href: str) -> tuple[str, list[dict]]:
    """The marketing team's decisions over the business facts, with how many facts each one rests on."""
    try:
        data = json.loads(result.content)
    except ValueError:
        data = {}
    ids = []
    for raw in data.get("decision_ids", []):
        try:
            ids.append(UUID(raw))
        except ValueError:
            continue
    rows = list(ctx.db.scalars(select(Decision).where(Decision.product_id == ctx.product.id, Decision.id.in_(ids)))) if ids else []
    rows.sort(key=lambda row: ids.index(row.id))  # the order the team gave them in
    text = "Команда разобрала цифры бизнеса."
    if data.get("summary"):
        text += " " + _cut(str(data["summary"]), 600)
    blocks: list[dict] = []
    if rows:
        table = []
        for row in rows:
            facts = len(row.evidence_references or [])
            note = f" (опирается на показателей: {facts})" if facts else " (без опоры на цифры)"
            table.append([DECISION_LABELS.get(row.decision_type.value, row.decision_type.value),
                          _cut(row.rationale, 380) + note])
        blocks.append({"type": "table", "columns": ["Решение", "Что это значит"], "rows": table})
    proposed = len(data.get("task_ids", []))
    if proposed:
        text += f" Предложено задач: {proposed}; они в разделе «В работе»."
    blocks.append({"type": "chips", "items": [{"label": "Показать цифры", "capability": "show_insights", "params": {}}]})
    blocks.append({"type": "links", "items": [{"label": "Открыть разбор", "href": href}]})
    return text, blocks


def _completed(ctx: Context, task: Task, title: str, href: str) -> tuple[str, list[dict]]:
    base = f"/studio/products/{ctx.product.id}"
    result = _result(ctx, task)
    if result is None:
        return f"Готово: {title}.", [{"type": "links", "items": [{"label": "Открыть", "href": href}]}]
    if result.kind == "business_review":
        return _business_review(ctx, result, href)
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


def _pending(db: Session, ctx: Context, conversation: DispatcherConversation):
    """Finished tasks started from this account's conversations with the company (an earlier topic too)
    that have not been reported with this outcome yet."""
    threads = select(DispatcherConversation.id).where(DispatcherConversation.product_id == ctx.product.id,
                                                      DispatcherConversation.account == conversation.account)
    reported = {(row.task_id, (row.params or {}).get("status")) for row in db.scalars(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id.in_(threads), DispatcherMessage.capability == RESULT_CAPABILITY))}
    rows = db.execute(select(DispatcherMessage, Task).join(Task, Task.id == DispatcherMessage.task_id).where(
        DispatcherMessage.conversation_id.in_(threads), DispatcherMessage.role == "assistant",
        or_(DispatcherMessage.capability.is_(None), DispatcherMessage.capability != RESULT_CAPABILITY),
        Task.product_id == ctx.product.id, Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
    ).order_by(DispatcherMessage.created_at, DispatcherMessage.seq)).all()
    result, seen = [], set()
    for message, task in rows:
        key = (task.id, task.status.value)
        if key not in reported and key not in seen:
            seen.add(key)
            result.append((message, task))
    return result


def collect_updates(db: Session, ctx: Context, conversation: DispatcherConversation) -> list[DispatcherMessage]:
    """Add one message per newly finished task to the current conversation and return the new messages."""
    from app.dispatcher.blocks import clean_blocks
    from app.dispatcher.service import _add_committed
    if not _pending(db, ctx, conversation):
        return []  # the usual case: nothing finished, no lock taken
    db.scalar(select(DispatcherConversation).where(DispatcherConversation.id == conversation.id).with_for_update())
    added: list[DispatcherMessage] = []
    for message, task in _pending(db, ctx, conversation):  # re-read under the lock: another tab may have reported
        status = task.status.value
        block = _task_block(message, task)
        title = (block or {}).get("title") or task.title.split(": ", 1)[-1]
        href = (block or {}).get("href") or f"/studio/products/{ctx.product.id}/tasks/{task.id}"
        if status == "completed":
            text, blocks = _completed(ctx, task, title, href)
        else:
            text = f"Не получилось: {title}. Подробности на странице задания."
            blocks = [{"type": "links", "items": [{"label": "Открыть задание", "href": href}]}]
        added.append(_add_committed(
            db, conversation, role="assistant", input_mode="text", text=text, spoken=text,
            blocks=clean_blocks([{"type": "text", "text": text}, *blocks]), intent="answer",
            capability=RESULT_CAPABILITY, params={"status": status}, task_id=task.id))
    db.commit()  # release the lock when nothing was added
    return added
