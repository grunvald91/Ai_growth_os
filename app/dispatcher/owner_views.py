"""Owner's immediate read-only views grounded in actual company records.

Do not invoke external LLMs or invent marketing figures just to answer a
request to see what's been made or what needs attention today.
"""
from __future__ import annotations

from sqlalchemy import select

from app.dispatcher.outcome import Context, Outcome, links_block
from app.dispatcher.registry import page_href
from app.dispatcher.readers import ACTIVE, attention, plans, work_rows
from app.models.domain import Artifact, Task


VIDEO_STATES = {
    "waiting_approval": "Нужно подтвердить",
    "queued": "В очереди",
    "running": "Создаётся",
    "completed": "Задание завершено",
    "failed": "Ошибка создания",
    "cancelled": "Отменено",
}


def _state(item) -> str:
    return getattr(item.status, "value", str(item.status))


def _task_url(ctx: Context, task: Task) -> str:
    section = "whiteboard" if task.assigned_agent == "whiteboard_renderer" else "tasks"
    return f"/studio/products/{ctx.product.id}/{section}/{task.id}"


async def show_recent_videos(ctx: Context, params) -> Outcome:
    """List recent video jobs AND rendered assets, including completed ones."""
    jobs = list(ctx.db.scalars(
        select(Task).where(
            Task.product_id == ctx.product.id,
            Task.assigned_agent.in_(("whiteboard_renderer", "content_renderer")),
        ).order_by(Task.created_at.desc(), Task.id.desc()).limit(12)
    ))
    artifacts = list(ctx.db.scalars(
        select(Artifact).where(
            Artifact.product_id == ctx.product.id,
            Artifact.kind.in_(("whiteboard_video", "video_render")),
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(16)
    ))
    by_task: dict = {}
    for artifact in artifacts:
        if artifact.task_id is not None and artifact.task_id not in by_task:
            by_task[artifact.task_id] = artifact

    items: list[dict] = []
    linked_tasks = set()
    for task in jobs:
        linked_tasks.add(task.id)
        artifact = by_task.get(task.id)
        title = (task.description or task.title or "Видео").strip()[:100]
        href = _task_url(ctx, task)
        if artifact is not None and artifact.kind == "video_render":
            href = f"/studio/products/{ctx.product.id}/materials/{artifact.id}"
        status = VIDEO_STATES.get(_state(task), _state(task))
        if _state(task) == "completed" and artifact is not None:
            status = "Готово к просмотру"
        items.append({"title": title, "status": status, "href": href,
                      "created": task.created_at.isoformat() if task.created_at else ""})
    for artifact in artifacts:
        if artifact.task_id in linked_tasks:
            continue
        if artifact.kind == "whiteboard_video" and artifact.task_id is not None:
            href = f"/studio/products/{ctx.product.id}/whiteboard/{artifact.task_id}"
        else:
            href = f"/studio/products/{ctx.product.id}/materials/{artifact.id}"
        status = _state(artifact)
        items.append({
            "title": (artifact.title or "Видео").strip()[:100],
            "status": "Материал подготовлен" if status in ("draft", "awaiting_approval") else status,
            "href": href,
            "created": artifact.created_at.isoformat() if artifact.created_at else "",
        })
    # Select the eight most recent, but display oldest first so that
    # the newest video is immediately above the input field.
    items.sort(key=lambda item: item["created"], reverse=True)
    items = list(reversed(items[:8]))
    if not items:
        return Outcome(
            "Не нашёл видео или заданий на видео для этой компании.",
            "Видео пока не нашлись.",
            [links_block(ctx, "whiteboard", "work")],
        )

    ready = sum(1 for item in items if item["status"] == "Готово к просмотру")
    text = f"Нашёл {len(items)} последних видео и заданий на видео."
    if ready:
        text += f" Из них готовы к просмотру: {ready}."
    return Outcome(
        text,
        f"Нашёл {len(items)} видео и заданий. Статусы и ссылки уже на экране.",
        [
            {"type": "resource_list", "items": [
                {"title": item["title"], "status": item["status"], "href": item["href"]}
                for item in items
            ]},
        ],
    )


async def recommend_today(ctx: Context, params) -> Outcome:
    """Actionable next steps from current work and planned materials, not made-up analytics."""
    current = work_rows(ctx.db, ctx.product.id)
    notes = attention(ctx.db, ctx.product.id)
    active_plan, draft_plan = plans(ctx.db, ctx.product.id)

    steps: list[tuple[str, str, str, str]] = []
    failed = sum(1 for note in notes if note.get("kind") == "error")
    waiting = sum(1 for note in notes if note.get("kind") == "review")
    active = sum(1 for job in current if job.get("state") in ACTIVE)

    if failed:
        steps.append(("Проверить ошибки заданий", f"В уведомлениях {failed} ошибок.", "work", "Открыть задания"))
    if waiting:
        steps.append(("Проверить готовые материалы и подтверждения",
                      f"Есть {waiting} событий, требующих решения.", "review", "Открыть проверку"))
    if active:
        steps.append(("Посмотреть ход текущих задач", f"Сейчас в работе {active} заданий.", "work", "Посмотреть работу"))
    if draft_plan:
        steps.append(("Посмотреть черновик контент-плана", "Есть новый черновик плана.", "plan", "Открыть план"))
    elif active_plan:
        steps.append(("Проверить ближайшие публикации", "Есть действующий контент-план.",
                      "calendar", "Открыть календарь"))
    else:
        steps.append(("Определить темы публикаций", "У компании нет утверждённого контент-плана.",
                      "plan", "Контент-план"))

    # The booking KPI analysis must read real source records when requested.
    # Do not claim current sales, client load or other metrics without that check.
    steps.append(("Проверить цифры бизнеса", "Записи, отмены и загрузку нужно сверить с источником.",
                  "analytics", "Открыть аналитику"))
    steps = steps[:5]
    links = [{"label": label, "href": page_href(page, ctx.product.id)}
             for _, _, page, label in steps]

    return Outcome(
        "Вот что можно сделать сегодня на основании текущих задач и материалов. "
        "Это приоритеты по состоянию платформы, а не прогноз выручки.",
        "Предлагаю начать с задач и материалов, требующих внимания. Шаги на экране.",
        [
            {"type": "table", "columns": ["Шаг", "Основание"],
             "rows": [[title, reason] for title, reason, _, _ in steps]},
            {"type": "links", "items": links},
            {"type": "chips", "items": [
                {"label": "Разобрать показатели записи", "capability": "show_insights", "params": {}},
                {"label": "Показать последние видео", "capability": "show_recent_videos", "params": {}},
            ]},
        ],
    )
