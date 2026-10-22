"""What each ability does. Only these functions touch the platform.

Every executor works through existing services (the same ones the Studio pages use), so tasks
keep their duplicate protection, owner approvals and audit records. Nothing here publishes anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.dispatcher.readers import ACTIVE, attention, latest_active_video, plans, upcoming_slots, work_rows
from app.dispatcher.registry import (CAPABILITIES, CHANNEL_LABELS, CancelVideoParams, GoToParams,
                                     PostParams, VideoParams, page_href, page_label)
from app.models.domain import Approval, Product, Task


@dataclass
class Context:
    db: Session
    product: Product
    settings: object
    account: str


@dataclass
class Confirmation:
    """A card asking before something with consequences. `params` come back when it is confirmed."""
    title: str
    details: list[str]
    params: dict
    paid: bool = False
    confirm_label: str = "Подтвердить"


@dataclass
class Outcome:
    text: str
    spoken: str | None = None
    blocks: list[dict] = field(default_factory=list)
    task_id: UUID | None = None
    confirmation: Confirmation | None = None
    ok: bool = True
    # Nothing was done because something is missing (for example no data yet).
    no_data: bool = False


def _task_block(ctx: Context, task: Task, title: str, href: str) -> dict:
    return {"type": "task", "task_id": str(task.id), "title": title,
            "status": getattr(task.status, "value", str(task.status)), "href": href}


def _links(ctx: Context, *keys: str) -> dict:
    return {"type": "links", "items": [{"label": page_label(k), "href": page_href(k, ctx.product.id)} for k in keys]}


# ---- reading -----------------------------------------------------------------------------

async def show_work(ctx: Context, params) -> Outcome:
    from app.api.content_studio_activity import work_url
    rows = work_rows(ctx.db, ctx.product.id)
    active = [row for row in rows if row["state"] in ACTIVE]
    if not active:
        return Outcome("Сейчас ничего не выполняется.", "Сейчас ничего не выполняется.",
                       [_links(ctx, "work")])
    table = {"type": "table", "columns": ["Задание", "Статус"],
             "rows": [[row["title"], row["label"]] for row in active]}
    spoken = f"В работе {len(active)}. Подробности на экране."
    links = {"type": "links", "items": [{"label": f"Открыть: {row['title'][:60]}", "href": work_url(ctx.product.id, row)}
                                        for row in active]}
    return Outcome(f"В работе: {len(active)}.", spoken, [table, links])


async def show_review(ctx: Context, params) -> Outcome:
    notes = attention(ctx.db, ctx.product.id)
    if not notes:
        return Outcome("Ничего не ждёт вашего решения.", "Ничего не ждёт вашего решения.", [_links(ctx, "review")])
    links = {"type": "links", "items": [{"label": note["text"][:80], "href": note["url"]} for note in notes]}
    return Outcome(f"Требует внимания: {len(notes)}.", f"Требует внимания: {len(notes)}. Они на экране.", [links])


async def show_plan(ctx: Context, params) -> Outcome:
    active, draft = plans(ctx.db, ctx.product.id)
    if active is None and draft is None:
        return Outcome("Контент-плана пока нет. Могу поставить задание его составить.",
                       "Контент-плана пока нет.", [_links(ctx, "plan")])
    blocks: list[dict] = []
    if active is not None:
        slots = upcoming_slots(active[1])
        from app.api.content_studio import platform_label
        if slots:
            blocks.append({"type": "table", "columns": ["Когда", "Площадка", "Тема"],
                           "rows": [[slot["_when"].astimezone().strftime("%d.%m %H:%M"),
                                     platform_label(slot.get("platform")), str(slot.get("topic", ""))]
                                    for slot in slots[:8]]})
        text = f"План утверждён. Впереди публикаций: {len(slots)}." + (" Показаны ближайшие." if len(slots) > 8 else "")
    else:
        text = "Утверждённого плана нет."
    if draft is not None:
        text += " Есть черновик нового плана, он ждёт утверждения."
    blocks.append(_links(ctx, "plan"))
    return Outcome(text, text, blocks)


async def go_to(ctx: Context, params: GoToParams) -> Outcome:
    label = page_label(params.target)
    return Outcome(f"{label} — вот здесь.", f"{label} — на экране кнопка.",
                   [{"type": "links", "items": [{"label": label, "href": page_href(params.target, ctx.product.id)}]}])


async def show_schedule(ctx: Context, params) -> Outcome:
    # Honest absence: the booking platform is not connected to the Studio yet.
    return Outcome("Расписание записей я пока не вижу — связь с онлайн-записью ещё не подключена. "
                   "Открыть его можно в админке ServGo.",
                   "Расписание записей я пока не вижу. Оно доступно в админке ServGo.",
                   [_links(ctx, "schedule")], no_data=True)


# ---- creating work -----------------------------------------------------------------------

async def create_post(ctx: Context, params: PostParams) -> Outcome:
    from app.content.service import queue_content
    try:
        task = queue_content(ctx.db, ctx.product.id, params.topic, format="post", channel=params.channel,
                             audience=params.audience)
    except (ValueError, LookupError):
        ctx.db.rollback()
        return Outcome("Не получилось поставить пост в работу: проверьте тему и площадку.",
                       "Не получилось поставить пост в работу.", ok=False)
    label = CHANNEL_LABELS[params.channel]
    text = f"Принял. Пишу пост для {label} на тему «{params.topic}». Сообщу, когда будет готов."
    return Outcome(text, f"Принял, пишу пост для {label}. Скоро будет готов.",
                   [_task_block(ctx, task, f"Пост для {label}: {params.topic}",
                                f"/studio/products/{ctx.product.id}/tasks/{task.id}")], task_id=task.id)


async def make_plan(ctx: Context, params) -> Outcome:
    from app.content.calendar import queue_plan
    try:
        task = queue_plan(ctx.db, ctx.product.id)
    except LookupError:
        ctx.db.rollback()
        return Outcome("Компания не найдена.", ok=False)
    text = "Принял. Составляю контент-план. Он ничего не опубликует: сначала я покажу его на утверждение."
    return Outcome(text, "Принял, составляю контент-план. Покажу его на утверждение.",
                   [_task_block(ctx, task, "Контент-план", f"/studio/products/{ctx.product.id}/tasks/{task.id}")],
                   task_id=task.id)


async def describe_approve_plan(ctx: Context, params) -> Outcome | Confirmation:
    _, draft = plans(ctx.db, ctx.product.id)
    if draft is None:
        return Outcome("Черновика плана на утверждение нет. Могу составить новый.", "Черновика плана нет.", ok=False)
    row, plan = draft
    count = len(plan.get("slots") or [])
    return Confirmation(
        title="Утвердить контент-план?",
        details=[f"Публикаций в плане: {count}.", CAPABILITIES["approve_plan"].consequence,
                 "Сами публикации по-прежнему подтверждаются отдельно."],
        params={"plan_id": str(row.id)})


async def confirm_approve_plan(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.content.calendar import approve_plan
    from app.models.domain import Artifact
    try:
        plan_id = UUID(str(stored["plan_id"]))
    except (KeyError, ValueError):
        return Outcome("Не нашёл план для утверждения.", ok=False)
    row = ctx.db.get(Artifact, plan_id)
    if row is None or row.product_id != ctx.product.id:
        return Outcome("Не нашёл план для утверждения.", ok=False)
    try:
        approve_plan(ctx.db, ctx.settings, plan_id)
    except ValueError as exc:
        ctx.db.rollback()
        return Outcome(str(exc), str(exc), ok=False)
    ctx.db.add(Approval(product_id=ctx.product.id, artifact_id=plan_id, action_type="dispatcher:approve_plan",
                        risk_level="YELLOW", status="approved", requested_by=ctx.account, decided_by=ctx.account,
                        decided_at=datetime.now(timezone.utc), reason=f"via={via}"))
    ctx.db.commit()
    return Outcome("План утверждён. Команда начнёт готовить материалы.", "План утверждён.", [_links(ctx, "plan")])


async def whiteboard_video(ctx: Context, params: VideoParams) -> Outcome:
    from app.api.whiteboard import CreateJob, _queue, job_state
    try:
        task = _queue(ctx.db, ctx.product, CreateJob(topic=params.topic), ctx.settings)
        state = job_state(ctx.db, ctx.product.id, task.id)
    except HTTPException as exc:
        ctx.db.rollback()
        return Outcome(str(exc.detail), ok=False)
    href = f"/studio/products/{ctx.product.id}/whiteboard/{task.id}"
    if state["status"] != "waiting_approval":
        return Outcome(f"Такое видео уже в работе: «{params.topic}».", "Такое видео уже в работе.",
                       [_task_block(ctx, task, f"Видео: {params.topic}", href)], task_id=task.id)
    confirmation = Confirmation(
        title="Запустить рисованное видео?",
        details=[f"Тема: {params.topic}.",
                 f"Это платная генерация: до {state['max_api_calls']} обращений к ИИ.",
                 "Пока вы не подтвердите, ничего не списывается."],
        params={"task_id": str(task.id), "request_digest": state["request_digest"]},
        paid=True, confirm_label="Запустить")
    return Outcome("Подготовил задание на видео. Оно платное, поэтому нужно ваше подтверждение.",
                   f"Подготовил задание на видео «{params.topic}». Это платная генерация. Запускаем?",
                   [_task_block(ctx, task, f"Видео: {params.topic}", href)], task_id=task.id,
                   confirmation=confirmation)


async def confirm_whiteboard_video(ctx: Context, stored: dict, via: str) -> Outcome:
    from app.api.whiteboard import ApproveJob, _approve
    try:
        task_id = UUID(str(stored["task_id"]))
        body = ApproveJob(request_digest=str(stored["request_digest"]), confirmed=True)
        await _approve(ctx.db, ctx.product.id, task_id, body, ctx.account, ctx.settings)
    except (KeyError, ValueError):
        return Outcome("Подтверждение относится к другой версии задания. Откройте видео и проверьте его.", ok=False)
    except HTTPException as exc:
        ctx.db.rollback()
        return Outcome(str(exc.detail), str(exc.detail), ok=False)
    href = f"/studio/products/{ctx.product.id}/whiteboard/{task_id}"
    return Outcome("Запустил рисованное видео. Оно появится в ленте, когда будет готово.",
                   "Запустил рисованное видео.", [{"type": "links", "items": [{"label": "Ход создания", "href": href}]}],
                   task_id=task_id)


async def cancel_video(ctx: Context, params: CancelVideoParams) -> Outcome:
    from app.content.whiteboard.jobs import cancel_job
    if params.task_id:
        try:
            task = ctx.db.get(Task, UUID(params.task_id))
        except ValueError:
            task = None
        if task is None or task.product_id != ctx.product.id or task.assigned_agent != "whiteboard_renderer":
            return Outcome("Не нашёл такое видео.", "Не нашёл такое видео.", ok=False)
    else:
        task = latest_active_video(ctx.db, ctx.product.id)
        if task is None:
            return Outcome("Нет рисованного видео, которое можно отменить.", "Отменять нечего.", ok=False)
    cancel_job(ctx.db, ctx.product.id, task.id)
    return Outcome("Видео отменено.", "Видео отменено.", task_id=task.id)


# ---- refusals ----------------------------------------------------------------------------

REFUSALS = {
    "budget": "Бюджет я сам не меняю.", "pricing": "Цены я сам не меняю.",
    "access": "Доступы и права я сам не меняю.", "delete": "Удалять данные я сам не буду.",
    "credentials": "Ключи и пароли я не трогаю.", "billing": "Платёжные данные я не меняю.",
}
REFUSAL_PAGES = {"access": "profile", "pricing": "admin", "billing": "admin", "budget": "admin"}


async def restricted_action(ctx: Context, params) -> Outcome:
    reason = REFUSALS[params.kind]
    text = reason + " Это делается вручную владельцем в настройках."
    page = REFUSAL_PAGES.get(params.kind)
    return Outcome(text, reason, [_links(ctx, page)] if page else [], ok=False)


EXECUTORS = {
    "show_work": show_work, "show_review": show_review, "show_plan": show_plan, "go_to": go_to,
    "show_schedule": show_schedule, "create_post": create_post, "make_plan": make_plan,
    "whiteboard_video": whiteboard_video, "cancel_video": cancel_video, "restricted_action": restricted_action,
}
# Abilities that always ask first: describe what will happen, run it only after a confirmation.
DESCRIBERS = {"approve_plan": describe_approve_plan}
CONFIRMERS = {"approve_plan": confirm_approve_plan, "whiteboard_video": confirm_whiteboard_video}
