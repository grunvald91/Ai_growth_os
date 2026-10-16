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

WORK_AGENTS = ("content_factory", "content_creative", "content_editor", "content_renderer", "editorial_planner", "whiteboard_renderer")

STATES = {
    "queued": ("В очереди", "queued", "Задание принято. Ожидает свободного обработчика."),
    "running": ("Создаётся", "running", "ИИ-команда работает. Можно закрыть страницу."),
    "waiting_approval": ("Нужно подтверждение", "review", "Проверьте тему и подтвердите создание видео. Платные вызовы требуют отдельного согласия."),
    "completed": ("Подготовлено", "done", "Задание завершено. Результаты доступны ниже."),
    "failed": ("Ошибка подготовки", "error", "Задание остановилось с ошибкой, а не исчезло."),
    "cancelled": ("Отменено", "error", "Создание остановлено."),
}
ANIMATOR_PHASES = {
    "waiting_approval": "Нужно подтвердить создание",
    "queued": "Ожидает запуска",
    "preflight": "Проверка доступности моделей",
    "storyboard": "Подготовка сценария",
    "image": "Создание рисунков",
    "regions": "Подготовка анимации",
    "speech": "Создание озвучки",
    "render": "Сборка видеофайла",
    "completed": "Видеофайл готов",
    "failed": "Ошибка подготовки",
    "cancelled": "Отменено",
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
    "whiteboard_storyboard": "Подготовка сценария",
    "whiteboard_image": "Создание рисунков",
    "whiteboard_regions": "Определение анимации рисунка",
    "whiteboard_speech": "Создание озвучки",
    "content_strategist": "Изучение темы и аудитории",
    "seo_editor": "Поисковые запросы и план текста",
    "article_writer": "Написание статьи",
    "human_editor": "Редактура для читателя",
    "repurposing_editor": "Версии для площадок",
    "video_director": "Сценарий короткого видео",
    "story_director": "Сценарий Stories",
    "visual_designer": "Описание изображения",
    "platform_writer": "Тексты для площадок",
    "youtube_shorts_director": "Сценарий YouTube Shorts",
    "instagram_reels_director": "Сценарий Reels",
    "post_rewriter": "Доработка поста",
}
PIPELINE_ORDER = ("content_strategist", "seo_editor", "article_writer", "conversion_editor", "human_editor",
                  "marketing_editor", "repurposing_editor", "video_director", "story_director", "visual_designer",
                  "creative_strategist", "creative_director", "post_strategist", "post_writer_editor")
STAGE_ROLES = {
    "creative_strategist": "Выясняет, кому и зачем снимаем",
    "creative_director": "Пишет сценарий по кадрам",
    "post_strategist": "Выбирает зацепку и главную мысль",
    "post_writer_editor": "Пишет пост и сам его вычитывает",
    "content_strategist": "Выясняет, кому и зачем пишем",
    "seo_editor": "Подбирает запросы и порядок текста",
    "article_writer": "Пишет первый вариант статьи",
    "conversion_editor": "Продумывает призыв и способ измерить результат",
    "human_editor": "Делает текст живым и понятным",
    "marketing_editor": "Усиливает текст и убирает лишнее",
    "repurposing_editor": "Готовит версии под площадки",
}
RESULT_KINDS = {"article", "channel_text", "video_script", "creative_brief", "video_render",
                "content_distribution", "dzen_brief", "telegram_brief",
                "short_video_brief", "story_brief", "static_image_brief", "editorial_plan", "whiteboard_video"}


def safe(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def readable_artifact_title(artifact: Artifact) -> str:
    """Never show internal derivative-brief names to the business owner."""
    raw = str(artifact.title or "").strip()
    normalized = raw.casefold().replace("_", " ").replace("-", " ")
    labels = {
        "telegram derivative brief": "Задание для Telegram",
        "dzen derivative brief": "Задание для Дзена",
        "short video derivative brief": "Сценарий короткого видео",
        "static image derivative brief": "Бриф изображения",
        "story derivative brief": "Бриф Stories",
    }
    return labels.get(normalized, raw or "Материал")


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
    if task.assigned_agent == "whiteboard_renderer":
        return task.description or "Рисованное видеообъяснение"
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
    # Pin unfinished jobs, including long-running items which would otherwise
    # vanish if more than 'limit' newer tasks were scheduled by the calendar.
    base = (Task.product_id == product_id, Task.assigned_agent.in_(WORK_AGENTS))
    pinned = list(db.scalars(select(Task).where(
        *base, Task.status.in_([
            TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL,
        ])
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(max(10, limit // 2))))
    recent = list(db.scalars(select(Task).where(
        *base,
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)))
    tasks = pinned + [row for row in recent if row.id not in {task.id for task in pinned}]
    tasks = tasks[:max(limit, len(pinned))]
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
    runs = list(db.scalars(select(AgentRun).where(
        AgentRun.task_id.in_(ids),
    ).order_by(AgentRun.started_at, AgentRun.id)))
    by_runs = {}
    for run in runs:
        by_runs.setdefault(run.task_id, []).append(run)
    results = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(ids),
        Artifact.kind.in_(RESULT_KINDS),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    by_results = {}
    for result in results:
        by_results.setdefault(result.task_id, []).append(result)
    animator_states = {record.task_id: record for record in db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(ids),
        Artifact.kind == "whiteboard_job_state",
    ))}
    output = []
    for task in tasks:
        key = task_state(task)
        title, kind, explanation = STATES.get(key, ("Проверить статус", "queued", "Состояние задачи уточняется"))
        items = by_results.get(task.id, [])
        animator_stage = None
        animator_calls = None
        if task.assigned_agent == "whiteboard_renderer" and task.id in animator_states:
            try:
                animator_data = json.loads(animator_states[task.id].content)
                animator_stage = animator_data.get("stage")
                animator_calls = animator_data.get("calls")
            except (TypeError, ValueError):
                pass
        # 'completed' means the worker finished its job, not that a material
        # was approved or that a video file exists.
        output.append({
            "id": str(task.id),
            "agent": task.assigned_agent,
            "title": task_title(task, by_request.get(task.id)),
            "state": key,
            "label": title,
            "tone": kind,
            "description": explanation,
            "artifacts": [{
                "id": str(art.id), "kind": art.kind,
                "title": readable_artifact_title(art),
                "status": getattr(art.status, "value", str(art.status)),
            } for art in items[:15]],
            "animator_stage": animator_stage,
            "animator_calls": animator_calls,
            "stage_signature": "|".join(
                str(run.id) + ":" + run.status
                for run in by_runs.get(task.id, [])[-20:]
            ),
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
        Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit)))
    for row in candidates:
        is_animator = row.kind == "whiteboard_video"
        notes.append({
            "kind": "review", "text": (
                "Рисованное видео готово: " if is_animator else
                "Готов MP4: " if row.kind == "video_render" else
                "Материал готов к проверке: "
            ) + readable_artifact_title(row)[:95],
            "url": (f"/studio/products/{product_id}/whiteboard/{row.task_id}"
                     if is_animator else f"/studio/products/{product_id}/materials/{row.id}"),
            "id": str(row.id),
        })
    # Some historical pipeline outputs are saved as draft rather than as
    # awaiting_approval. Surface recent completed outputs without claiming
    # they were approved or published.
    recent_drafts = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.status == ArtifactStatus.DRAFT,
        Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
        Artifact.created_at >= datetime.now(timezone.utc) - timedelta(days=3),
    ).order_by(Artifact.created_at.desc()).limit(4)))
    known = {note["id"] for note in notes}
    for draft in recent_drafts:
        if str(draft.id) not in known:
            notes.append({
                "kind": "review",
                "text": "Материал подготовлен: " + readable_artifact_title(draft)[:95],
                "url": f"/studio/products/{product_id}/materials/{draft.id}",
                "id": str(draft.id),
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
    # Approval is a separate required owner decision before paid Animator
    # providers are called. Show it as an actionable alert.
    awaiting = list(db.scalars(select(Task).where(
        Task.product_id == product_id,
        Task.assigned_agent == "whiteboard_renderer",
        Task.status == TaskStatus.WAITING_APPROVAL,
    ).order_by(Task.created_at.desc()).limit(5)))
    for task in awaiting:
        notes.append({
            "kind": "review",
            "text": "Подтвердите создание рисованного видео (платные API)",
            "url": f"/studio/products/{product_id}/whiteboard/{task.id}",
            "id": str(task.id),
        })
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
            "url": (f"/studio/products/{product_id}/whiteboard/{task.id}"
                     if task.assigned_agent == "whiteboard_renderer"
                     else f"/studio/products/{product_id}/tasks/{task.id}"),
            "id": str(task.id),
        })
    return notes[:limit]


def notice_html(product_id: UUID) -> str:
    """Small accessible dropdown. The list is populated by an authenticated poll."""
    return f"""
    <details class="sg-notifications" data-sg-notifications
             data-feed-url="/studio/products/{product_id}/activity.json">
      <summary aria-label="Уведомления о материалах">
        <span aria-hidden="true">🔔</span>
        <span class="sg-notify-label">Уведомления</span>
        <span class="sg-notify-count" data-sg-count hidden>0</span>
      </summary>
      <div class="sg-notify-popover">
        <strong>Требует внимания</strong>
        <div data-sg-notify-list role="status" aria-live="polite">Загрузка уведомлений…</div>
        <a class="sg-notify-all" href="/studio/products/{product_id}/work">Все задания →</a>
      </div>
    </details>"""


def work_url(product_id: UUID, item: dict) -> str:
    base = f"/studio/products/{product_id}"
    kind = "whiteboard" if item.get("agent") == "whiteboard_renderer" else "tasks"
    return f"{base}/{kind}/{item['id']}"


def activity_html(product_id: UUID, items: list[dict], tab: str = "all") -> str:
    base = f"/studio/products/{product_id}"
    tabs = (("all", "Все"), ("active", "В работе"), ("approval", "Нужно решение"),
            ("ready", "Готово"), ("problems", "Ошибки"))
    selector = '<nav class="sg-work-tabs" aria-label="Состояния заданий">' + "".join(
        f'<a href="{base}/work?tab={name}" class="{"active" if name==tab else ""}">'
        f'{label}</a>' for name, label in tabs
    ) + '</nav>'
    visible = [i for i in items if (
        tab == "all" or
        (tab == "active" and i["state"] in {"queued", "running"}) or
        (tab == "approval" and i["state"] == "waiting_approval") or
        (tab == "ready" and i["state"] == "completed") or
        (tab == "problems" and i["state"] in {"failed", "cancelled"})
    )]
    cards = []
    for item in visible:
        artifact_count = len(item["artifacts"])
        label = item["label"]
        if item["state"] == "completed" and artifact_count:
            label = "Материалы готовы"
        if item.get("agent") == "whiteboard_renderer":
            stage = ANIMATOR_PHASES.get(str(item.get("animator_stage")), item["description"])
            calls = item.get("animator_calls")
            summary = stage + (f" · API-вызовов: {calls}" if isinstance(calls, int) else "")
        else:
            summary = (
                f'{artifact_count} материалов. Откройте результат и проверьте перед публикацией.'
                if artifact_count else item["description"]
            )
        cards.append(
            f'<a class="sg-work-item sg-work-{item["tone"]}" href="{work_url(product_id, item)}">'
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
    working = [x for x in items if x["state"] in {"queued", "running", "waiting_approval"}]
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
            f'<a class="sg-work-item sg-work-{item["tone"]}" href="{work_url(product_id, item)}">'
            f'<div class="sg-work-item-main"><b>{safe(item["title"])}</b>'
            f'<p>{safe(ANIMATOR_PHASES.get(str(item.get("animator_stage")), item["description"]) if item.get("agent") == "whiteboard_renderer" else item["description"])}</p></div>'
            f'<span class="sg-work-status sg-work-{item["tone"]}">{safe(item["label"])}</span>'
            '<span class="sg-work-arrow" aria-hidden="true">→</span></a>'
        )
    return (
        '<section class="sg-surface sg-overview-work" data-sg-work-overview>'
        '<div class="sg-section-head"><div><h2>' +
        ("Сейчас создаётся" if working else "Последние задания") +
        '</h2><p>Задания не пропадут: вернуться можно в любой момент.</p></div>'
        f'<a class="sg-mini-link" href="{base}/work">Все задания →</a></div>'
        + "".join(cards) + '</section>'
    )


def _minutes(run: AgentRun) -> str:
    start, end = run.started_at, run.finished_at
    if not start or not end:
        return ""
    seconds = max(0, int((end - start).total_seconds()))
    return "меньше минуты" if seconds < 60 else f"{round(seconds / 60)} мин"


def task_detail_html(product_id: UUID, task: Task, runs: list[AgentRun],
                     results: list[Artifact], request: Artifact | None = None,
                     stage_notes: dict | None = None) -> str:
    from app.api.content_studio_guide import notes_html
    stage_notes = stage_notes or {}
    base = f"/studio/products/{product_id}"
    key = task_state(task)
    label, tone, description = STATES.get(key, ("Проверить", "queued", ""))
    count = len(results)
    steps = []
    if not runs:
        steps.append('<li class="sg-stage"><span class="sg-stage-mk">·</span><div class="sg-stage-body">'
                     '<p class="sg-stage-wait">' + ("Ожидаем запуска обработки." if key == "queued" else
                     "Обработка запущена. Подробные шаги ещё не поступили.") + '</p></div></li>')
    ordered = sorted(runs[-16:], key=lambda r: (r.started_at is None,
                     r.started_at.timestamp() if r.started_at else (PIPELINE_ORDER.index(r.agent_name) if r.agent_name in PIPELINE_ORDER else 99)))
    for n, run in enumerate(ordered, 1):
        stage = STAGE_NAMES.get(run.agent_name, "Подготовка материала")
        role = STAGE_ROLES.get(run.agent_name, "")
        css, state = {
            "queued": ("", "Ждёт очереди"), "running": ("run", "Работает"),
            "completed": ("done", "Готово"), "failed": ("err", "Ошибка"),
        }.get(run.status, ("", "Проверяется"))
        took = _minutes(run) if run.status == "completed" else ""
        notes = stage_notes.get(run.id)
        if notes:
            detail = ('<details class="sg-stage-out"><summary>Что вывел этот шаг</summary>'
                      f'<div class="sg-note-body">{notes_html(notes)}</div></details>')
        elif run.status == "running":
            detail = '<p class="sg-stage-wait">Работает. Вывод появится здесь, когда шаг закончится.</p>'
        elif run.status == "completed":
            detail = '<p class="sg-stage-wait">Вывод этого шага не сохранился отдельно. Итог смотрите в результатах ниже.</p>'
        elif run.status == "failed":
            detail = '<p class="sg-stage-wait">Шаг остановился с ошибкой. Задание можно повторить.</p>'
        else:
            detail = '<p class="sg-stage-wait">Начнёт после предыдущего шага.</p>'
        mark = "✓" if css == "done" else str(n)
        steps.append(
            f'<li class="sg-stage {css}"><span class="sg-stage-mk" aria-hidden="true">{mark}</span>'
            f'<div class="sg-stage-body"><div class="sg-stage-head"><div><h4>{safe(stage)}</h4>'
            + (f'<small>{safe(role)}</small>' if role else '') + '</div>'
            f'<span class="sg-stage-state">{safe(state)}{(" · " + safe(took)) if took else ""}</span></div>'
            f'{detail}</div></li>'
        )
    rendered = []
    for result in results:
        title = readable_artifact_title(result)[:100]
        link = (f"{base}/plan?view=draft" if result.kind == "editorial_plan"
                else f"{base}/materials/{result.id}")
        label = {"article": "Статья", "channel_text": "Готовый пост",
                 "video_script": "Сценарий видео", "creative_brief": "Раскадровка",
                 "video_render": "Видеофайл MP4",
                 "editorial_plan": "Новый контент-план"}.get(result.kind, "Материал")
        rendered.append(
            f'<a class="sg-work-result" href="{link}">'
            f'<div><span>{safe(label)}</span><b>{safe(title)}</b>'
            + ('<small>Это сценарий и кадры. MP4 создаётся отдельным заданием.</small>'
               if result.kind == "creative_brief" else '') + '</div>'
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
    stage_sig = "|".join(str(run.id) + ":" + run.status for run in runs[-20:])
    return (
        f'<section class="sg-surface sg-work-detail" data-sg-task="{task.id}" data-sg-state="{key}" data-sg-stage="{safe(stage_sig)}">'
        f'<span class="sg-work-status sg-work-{tone}">'
        f'{safe(label)}</span><h2>{safe(task_title(task, request))}</h2><p>{safe(explanation)}</p>'
        f'<div class="sg-work-steps"><h3>Как шла работа</h3><ol class="sg-stage-list">{"".join(steps)}</ol></div>'
        f'<div class="sg-work-results"><h3>Результаты</h3>'
        + ("".join(rendered) or '<p>Результаты появятся здесь после подготовки.</p>')
        + '</div>' + refresh + fail
        + f'<p><a class="sg-mini-link" href="{base}/work">← Все задания</a></p></section>'
    )
