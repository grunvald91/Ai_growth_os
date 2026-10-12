"""Owner-facing work tracking for Growth Studio. Read only; no fake progress.

The existing tasks, agent runs, artifacts and publication records are the source
of truth. A task is never shown as completed merely because an HTTP request
returned successfully.
"""
from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact, ArtifactStatus, Task, TaskStatus

WORK_AGENTS = ("content_factory", "content_creative", "content_editor", "content_renderer", "editorial_planner")

STATES = {
    "queued": ("В очереди", "queued", "Задание принято. Ожидает свободного обработчика."),
    "running": ("Создаётся", "running", "ИИ-команда работает. Можно закрыть страницу."),
    "waiting_approval": ("Ждёт решения", "review", "Проверьте готовые материалы."),
    "completed": ("Подготовлено", "done", "Задание завершено. Результаты доступны ниже."),
    "failed": ("Ошибка подготовки", "error", "Задание остановилось с ошибкой, а не исчезло."),
    "cancelled": ("Отменено", "error", "Создание остановлено."),
}
STAGE_NAMES = {
    "creative_strategist": "Изучение темы и аудитории",
    "creative_director": "Сценарий и раскадровка",
    "post_strategist": "Идея и структура поста",
    "post_writer_editor": "Написание и редактура",
    "content_renderer": "Создание видеофайла",
    "content_editor": "Доработка по замечаниям",
    "editorial_planner": "Подготовка контент-плана",
    "researcher": "Поиск информации",
    "marketing_strategist": "Маркетинговая стратегия",
    "marketing_editor": "Редактирование статьи",
    "conversion_editor": "Редактирование призыва к действию",
}
RESULT_KINDS = {"article", "channel_text", "video_script", "creative_brief", "video_render",
                "content_distribution", "dzen_brief", "telegram_brief",
                "short_video_brief", "story_brief", "static_image_brief"}


def safe(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def task_state(task: Task) -> str:
    return getattr(task.status, "value", str(task.status))


def task_title(task: Task, request: Artifact | None = None) -> str:
    """Friendly names instead of internal title/UUID strings."""
    data = {}
    if request:
        try:
            data = json.loads(request.content)
            if not isinstance(data, dict):
                data = {}
        except (ValueError, TypeError):
            pass
    topic = str(data.get("topic") or "").strip()
    if task.assigned_agent == "editorial_planner":
        return "Новый контент-план"
    if task.assigned_agent == "content_renderer":
        return "Создание видеофайлов"
    if task.assigned_agent == "content_editor":
        return "Исправление материала"
    if task.assigned_agent == "content_creative":
        labels = {"short_video": "Сценарий видео", "story": "Stories", "static_image": "Бриф изображения"}
        return labels.get(data.get("format"), "Визуальный материал") + (f": {topic}" if topic else "")
    if data.get("mode") == "post":
        label = {"telegram": "Telegram", "vk": "ВКонтакте", "dzen": "Дзен",
                 "vc_ru": "vc.ru", "tenchat": "TenChat"}.get(data.get("channel"), "соцсети")
        return f"Пост для {label}: {topic}" if topic else f"Пост для {label}"
    if topic:
        return f"Контент по теме: {topic}"
    title = task.title or ""
    for prefix in ("CONTENT: ", "POST: ", "CREATIVE: ", "RENDER: ", "EDIT: "):
        if title.startswith(prefix):
            title = title[len(prefix):]
            break
    if title.startswith("calendar:"):
        return "Материалы контент-плана"
    if len(title) >= 32 and all(c in "0123456789abcdef-" for c in title.lower()):
        return "Подготовка материала"
    return title[:125] or "Подготовка материала"


def work_items(db: Session, product_id: UUID, limit: int = 60) -> list[dict]:
    tasks = list(db.scalars(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent.in_(WORK_AGENTS)
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)))
    if not tasks:
        return []
    ids = [task.id for task in tasks]
    requests = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(ids),
        Artifact.kind.in_(("content_request", "creative_request", "edit_request")),
    ).order_by(Artifact.created_at.desc())))
    by_request = {}
    for record in requests:
        by_request.setdefault(record.task_id, record)
    results = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(ids),
        Artifact.kind.in_(RESULT_KINDS),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    by_results = {}
    for result in results:
        by_results.setdefault(result.task_id, []).append(result)
    output = []
    for task in tasks:
        key = task_state(task)
        title, kind, explanation = STATES.get(key, ("Проверить статус", "queued", "Состояние задачи уточняется"))
        items = by_results.get(task.id, [])
        # 'completed' means the worker finished its job, not that a material
        # was approved or that a video file exists.
        output.append({
            "id": str(task.id),
            "title": task_title(task, by_request.get(task.id)),
            "state": key,
            "label": title,
            "tone": kind,
            "description": explanation,
            "artifacts": [{
                "id": str(art.id), "kind": art.kind,
                "title": art.title,
                "status": getattr(art.status, "value", str(art.status)),
            } for art in items[:15]],
            "created_at": task.created_at.isoformat() if task.created_at else None,
        })
    return output


def notice_items(db: Session, product_id: UUID, limit: int = 8) -> list[dict]:
    """Actionable notifications: material approval, planner draft, failed job.

    No persistent 'read' flag is implied. These are live tasks awaiting action.
    """
    notes = []
    recent = datetime.now(timezone.utc) - timedelta(days=21)
    candidates = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
        Artifact.kind.in_(RESULT_KINDS - {"video_render"}),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit)))
    for row in candidates:
        notes.append({
            "kind": "review", "text": "Материал готов к проверке: " + row.title[:95],
            "url": f"/studio/products/{product_id}/materials/{row.id}",
            "id": str(row.id),
        })
    plans = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "editorial_plan",
    ).order_by(Artifact.created_at.desc()).limit(12)))
    for plan in plans:
        try:
            data = json.loads(plan.content)
        except (ValueError, TypeError):
            continue
        if data.get("approval_state") == "draft":
            notes.append({
                "kind": "review", "text": "Новый контент-план готов к утверждению",
                "url": f"/studio/products/{product_id}/plan?view=draft",
                "id": str(plan.id),
            })
            break
    failed = list(db.scalars(select(Task).where(
        Task.product_id == product_id,
        Task.assigned_agent.in_(WORK_AGENTS),
        Task.status == TaskStatus.FAILED,
    ).order_by(Task.created_at.desc()).limit(4)))
    for task in failed:
        if task.created_at and task.created_at.replace(tzinfo=task.created_at.tzinfo or timezone.utc) < recent:
            continue
        notes.append({
            "kind": "error", "text": "Не удалось подготовить материал — проверьте задание",
            "url": f"/studio/products/{product_id}/tasks/{task.id}",
            "id": str(task.id),
        })
    return notes[:limit]


def notice_html(product_id: UUID) -> str:
    """Small accessible dropdown. The list is populated by an authenticated poll."""
    return f"""
    <details class="sg-notifications" data-sg-notifications
             data-feed-url="/studio/products/{product_id}/activity.json">
      <summary aria-label="Уведомления о материалах">
        <span aria-hidden="true">♧</span>
        <span class="sg-notify-label">Уведомления</span>
        <span class="sg-notify-count" data-sg-count hidden>0</span>
      </summary>
      <div class="sg-notify-popover">
        <strong>Требует внимания</strong>
        <div data-sg-notify-list role="status" aria-live="polite">Загрузка уведомлений…</div>
        <a class="sg-notify-all" href="/studio/products/{product_id}/work">Все задания →</a>
      </div>
    </details>"""


def activity_html(product_id: UUID, items: list[dict], tab: str = "all") -> str:
    base = f"/studio/products/{product_id}"
    tabs = (("all", "Все"), ("active", "В работе"), ("ready", "Готово"),
            ("problems", "Ошибки"))
    selector = '<nav class="sg-work-tabs" aria-label="Состояния заданий">' + "".join(
        f'<a href="{base}/work?tab={name}" class="{"active" if name==tab else ""}">'
        f'{label}</a>' for name, label in tabs
    ) + '</nav>'
    visible = [i for i in items if (
        tab == "all" or
        (tab == "active" and i["state"] in {"queued", "running"}) or
        (tab == "ready" and i["state"] in {"completed", "waiting_approval"}) or
        (tab == "problems" and i["state"] in {"failed", "cancelled"})
    )]
    cards = []
    for item in visible:
        artifact_count = len(item["artifacts"])
        label = item["label"]
        if item["state"] == "completed" and artifact_count:
            label = "Материалы готовы"
        summary = (
            f'{artifact_count} материалов. Откройте результат и проверьте перед публикацией.'
            if artifact_count else item["description"]
        )
        cards.append(
            f'<a class="sg-work-item sg-work-{item["tone"]}" href="{base}/tasks/{item["id"]}">'
            f'<div class="sg-work-item-main"><b>{safe(item["title"])}</b>'
            f'<p>{safe(summary)}</p></div>'
            f'<span class="sg-work-status sg-work-{item["tone"]}">{safe(label)}</span>'
            '<span class="sg-work-arrow" aria-hidden="true">→</span></a>'
        )
    empty = ('<section class="sg-work-empty"><h3>Пока здесь пусто</h3>'
             '<p>Все созданные задания будут отображаться здесь, даже если вы закроете страницу.</p>'
             f'<a class="btn primary" href="{base}/create">Создать материал →</a></section>')
    return (
        '<section class="sg-surface"><div class="sg-section-head"><div>'
        '<h2>Создание материалов</h2>'
        '<p>Состояние берётся из реальных заданий. Не нужно запоминать, что и где запускалось.</p>'
        '</div><a class="btn primary" href="' + base + '/create">＋ Создать</a></div>'
        + selector + '<div class="sg-work-list">'
        + ("".join(cards) or empty) + '</div></section>'
    )


def activity_overview(product_id: UUID, items: list[dict]) -> str:
    base = f"/studio/products/{product_id}"
    working = [x for x in items if x["state"] in {"queued", "running"}]
    recent = items[:5]
    if not working and not recent:
        return (
            '<section class="sg-surface sg-overview-work"><div class="sg-section-head">'
            '<div><h2>Создание материалов</h2><p>Начните с одной темы.</p></div></div>'
            f'<a class="btn primary" href="{base}/create">＋ Создать материал</a></section>'
        )
    cards = []
    for item in (working[:4] or recent[:4]):
        cards.append(
            f'<a class="sg-work-item sg-work-{item["tone"]}" href="{base}/tasks/{item["id"]}">'
            f'<div class="sg-work-item-main"><b>{safe(item["title"])}</b>'
            f'<p>{safe(item["description"])}</p></div>'
            f'<span class="sg-work-status sg-work-{item["tone"]}">{safe(item["label"])}</span>'
            '<span class="sg-work-arrow" aria-hidden="true">→</span></a>'
        )
    return (
        '<section class="sg-surface sg-overview-work" data-sg-work-overview>'
        '<div class="sg-section-head"><div><h2>Сейчас в работе</h2>'
        '<p>Задания не пропадут: вернуться можно в любой момент.</p></div>'
        f'<a class="sg-mini-link" href="{base}/work">Все задания →</a></div>'
        + "".join(cards) + '</section>'
    )


def task_detail_html(product_id: UUID, task: Task, runs: list[AgentRun],
                     results: list[Artifact]) -> str:
    base = f"/studio/products/{product_id}"
    key = task_state(task)
    label, tone, description = STATES.get(key, ("Проверить", "queued", ""))
    count = len(results)
    steps = []
    if not runs:
        steps.append("<li>Ожидаем запуска обработки</li>" if key == "queued" else
                     "<li>Обработка запущена. Подробные этапы ещё не поступили.</li>")
    for run in runs[-16:]:
        stage = STAGE_NAMES.get(run.agent_name, "Подготовка материала")
        state = {
            "queued": "Ожидает", "running": "Выполняется",
            "completed": "Готово", "failed": "Ошибка",
        }.get(run.status, "Проверяется")
        steps.append(f'<li><span>{safe(stage)}</span><b>{safe(state)}</b></li>')
    rendered = []
    for result in results:
        title = result.title[:100] or "Материал"
        link = f"{base}/materials/{result.id}"
        label = {"article": "Статья", "channel_text": "Готовый пост",
                 "video_script": "Сценарий видео", "creative_brief": "Раскадровка",
                 "video_render": "Видеофайл MP4"}.get(result.kind, "Материал")
        rendered.append(
            f'<a class="sg-work-result" href="{link}">'
            f'<div><span>{safe(label)}</span><b>{safe(title)}</b></div>'
            '<span aria-hidden="true">Открыть →</span></a>'
        )
    if key == "completed" and not count:
        explanation = ('Обработка отмечена как завершённая, но готовый материал '
                       'пока не найден. Проверьте историю задания или сообщите об ошибке.')
    elif key == "completed" and count:
        explanation = f"Готово материалов: {count}. Откройте каждый, чтобы посмотреть и утвердить."
    else:
        explanation = description
    refresh = ('<p class="sg-work-refresh" role="status">Состояние обновляется каждые 15 секунд. '
               'Эту страницу можно закрыть и вернуться позже.</p>'
               if key in {"queued", "running"} else "")
    fail = (
        f'<form action="{base}/tasks/{task.id}/retry" method="post">'
        '<button class="btn secondary" type="submit">Повторить задание</button></form>'
        if key in {"failed", "cancelled"} and task.assigned_agent
        in {"content_factory", "content_creative", "content_editor"} and not results else ""
    )
    return (
        f'<section class="sg-surface sg-work-detail"><span class="sg-work-status sg-work-{tone}">'
        f'{safe(label)}</span><h2>{safe(task_title(task))}</h2><p>{safe(explanation)}</p>'
        f'<div class="sg-work-steps"><h3>Этапы создания</h3><ol>{"".join(steps)}</ol></div>'
        f'<div class="sg-work-results"><h3>Результаты</h3>'
        + ("".join(rendered) or '<p>Результаты появятся здесь после подготовки.</p>')
        + '</div>' + refresh + fail
        + f'<p><a class="sg-mini-link" href="{base}/work">← Все задания</a></p></section>'
    )
