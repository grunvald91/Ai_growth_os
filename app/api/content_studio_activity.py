"""Owner-facing work tracking for Growth Studio. Read only; no fake progress.

The existing tasks, agent runs, artifacts and publication records are the source
of truth. A task is never shown as completed merely because an HTTP request
returned successfully.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from uuid import UUID
from urllib.parse import urlencode
import hashlib

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact, ArtifactStatus, Task, TaskStatus
from app.api.studio_resource_widgets import resource_widget

# Only explicitly mapped, owner-visible jobs. Do not expose every internal Task.
WORK_AGENTS = ("content_factory", "content_creative", "content_editor", "content_renderer",
               "editorial_planner", "whiteboard_renderer", "seo_factory", "business_insights")
# These are read-only job reports; do not apply material approval semantics to them.
JOB_REPORT_KINDS = {"seo_factory_report", "business_review"}
SEO_PHASE_NAMES = {
    "prepare_queries": "Подготовка поисковых запросов",
    "collect_suggestions": "Сбор поисковых подсказок",
    "landing_opportunities": "Подготовка вариантов посадочных страниц",
    "build_plan": "Составление контент-плана",
    "queue_articles": "Постановка статей в очередь",
    "completed": "Планирование завершено",
}

STATES = {
    "queued": ("В очереди", "queued", "Задание принято. Ожидает свободного обработчика."),
    "running": ("Создаётся", "running", "ИИ-команда работает. Можно закрыть страницу."),
    "waiting_approval": ("Нужно подтверждение", "review", "Задание ожидает решения. Посмотрите детали, прежде чем продолжать."),
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
    "creative_image": "Генерация изображений",
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
}
RESULT_KINDS = {"article", "channel_text", "video_script", "creative_brief", "video_render",
                "content_distribution", "dzen_brief", "telegram_brief",
                "short_video_brief", "story_brief", "static_image_brief", "editorial_plan", "whiteboard_video"}


def safe(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def readable_artifact_title(artifact: Artifact) -> str:
    """Never show internal derivative-brief names to the business owner."""
    raw = str(artifact.title or "").strip()
    if artifact.kind == "whiteboard_video":
        # The kind already identifies the video. Don't repeat a pipeline prefix
        # like "Рисованное видео готово: Рисованное видео: <topic>" in the UI.
        raw = re.sub(r"^(?:(?:рисованное|простое) видео\s*:\s*)+", "", raw,
                     flags=re.IGNORECASE).strip() or "Видео без названия"
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


def is_seo_publish_proposal(task: Task) -> bool:
    """Daily growth review proposals are not article production or publication."""
    return (task.assigned_agent == "distribution" and
            bool(re.match(r"^Request approval to publish own-site SEO article\b",
                          str(task.title or ""), flags=re.I)))


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
    if task.assigned_agent == "company_intake":
        return "Профиль компании: сбор сведений"
    if task.assigned_agent == "seo_factory":
        return "SEO-завод: подготовка запросов и плана"
    if task.assigned_agent == "business_insights":
        return "Разбор данных бизнеса"
    if is_seo_publish_proposal(task):
        return "Предложение публикации SEO-статьи"
    # SEO-ARTICLE:<plan UUID>:<item id> is an idempotency key used by workers.
    # It is never a business-facing title; show the original plan topic instead.
    if (task.title or "").startswith("SEO-ARTICLE:"):
        return f"SEO-статья: {topic}" if topic else "SEO-статья по контент-плану"
    if task.assigned_agent == "whiteboard_renderer":
        return re.sub(r'^Рисованное видео:', 'Простое видео:', task.description or 'Простое видео', flags=re.I)
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


def task_public_description(task: Task) -> str:
    """Show truthful Russian owner copy without rewriting historical task keys."""
    if task.assigned_agent == "company_intake":
        return "Сведения о компании проверяются в разделе «Профиль компании»."
    if task.assigned_agent == "seo_factory":
        return "SEO-завод собирает поисковые запросы, план и ставит статьи в очередь. Это не подтверждение публикации."
    if task.assigned_agent == "business_insights":
        return "ИИ-команда изучает показатели компании и готовит выводы. Никаких публикаций это не запускает."
    if is_seo_publish_proposal(task):
        return ("Предложение ИИ-команды о публикации SEO-статьи. "
                "Оно не подтверждает готовность статьи и не запускает публикацию.")
    if (task.title or "").startswith("SEO-ARTICLE:"):
        return "Создание SEO-статьи по контент-плану."
    detail = str(task.description or "").strip()
    if detail == ("SEO article, channel adaptations, two video scripts. "
                  "No automatic publication or video rendering."):
        return ("Подготовка статьи, адаптаций для каналов и двух видеосценариев. "
                "Публикация и создание видеофайлов выполняются отдельно.")
    return detail


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
    return _work_items_for_tasks(db, product_id, tasks)


def _work_items_for_tasks(db: Session, product_id: UUID, tasks: list[Task]) -> list[dict]:
    """Hydrate only task IDs selected by the caller (avoids paginating in Python)."""
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
        Artifact.kind.in_(RESULT_KINDS | JOB_REPORT_KINDS),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    by_results = {}
    for result in results:
        by_results.setdefault(result.task_id, []).append(result)
    animator_states = {record.task_id: record for record in db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id.in_(ids),
        Artifact.kind == "whiteboard_job_state",
    ))}
    # Read SEO stage artifacts for the displayed jobs only, not the entire SEO log.
    seo_ids = [task.id for task in tasks if task.assigned_agent == "seo_factory"]
    latest_seo_stages = {}
    if seo_ids:
        stage_rows = list(db.scalars(select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_(seo_ids), Artifact.kind == "seo_factory_stage",
        ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
        for record in stage_rows:
            try:
                data = json.loads(record.content or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            stamp = str(data.get("recorded_at") or "")
            current = latest_seo_stages.get(record.task_id)
            if isinstance(data.get("stage"), str) and (
                current is None or (stamp, str(record.id)) > current[0]
            ):
                latest_seo_stages[record.task_id] = (
                    (stamp, str(record.id)), data["stage"]
                )
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
        # A completed worker may still have an artifact waiting for an owner.
        # Keep publication state separate from task completion.
        pending_artifacts = any(
            art.kind in RESULT_KINDS
            and getattr(art.status, "value", str(art.status)) == "awaiting_approval"
            for art in items
        )
        draft_plan = False
        for art in items:
            if art.kind != "editorial_plan":
                continue
            try:
                draft_plan = json.loads(art.content or "{}").get("approval_state") == "draft"
            except (ValueError, TypeError, AttributeError):
                pass
            if draft_plan:
                break
        needs_decision = key == "waiting_approval" or pending_artifacts or draft_plan
        recent_runs = by_runs.get(task.id, [])
        active_run = next((run for run in reversed(recent_runs)
                           if run.status == "running"), None)
        stage_name = STAGE_NAMES.get(active_run.agent_name, "Работа ИИ-команды") if active_run else None
        kind_name = {
            "content_factory": "Материал", "content_creative": "Креатив",
            "content_editor": "Редактирование", "content_renderer": "Видео",
            "editorial_planner": "Контент-план",
            "whiteboard_renderer": "Видео", "seo_factory": "SEO-завод",
            "business_insights": "Бизнес-анализ",
        }.get(task.assigned_agent, "Задание")
        special_summary = None
        display_label = "Нужно решение" if needs_decision else title
        if task.assigned_agent == "seo_factory":
            stage_row = latest_seo_stages.get(task.id)
            if key == "running" and stage_row:
                stage_name = SEO_PHASE_NAMES.get(stage_row[1], "Обработка SEO-запросов")
            elif key == "running":
                stage_name = "Подготовка поисковых запросов и плана"
            if key == "completed":
                # Finishing a factory job means queueing articles, not publishing.
                report_count = None
                for art in items:
                    if art.kind != "seo_factory_report":
                        continue
                    try:
                        report_data = json.loads(art.content or "{}")
                    except (TypeError, ValueError):
                        continue
                    if isinstance(report_data, dict) and type(report_data.get("articles_queued")) is int:
                        report_count = report_data["articles_queued"]
                        break
                display_label = "Планирование завершено"
                special_summary = (
                    f"Статей поставлено в очередь: {report_count}. Публикация выполняется отдельно."
                    if report_count is not None else
                    "Планирование завершено. Результаты и очередь статей — в SEO-заводе."
                )
        elif task.assigned_agent == "business_insights":
            if key == "running":
                stage_name = "Анализ показателей бизнеса"
            elif key == "completed":
                if any(art.kind == "business_review" for art in items):
                    display_label = "Разбор готов"
                    special_summary = "Подготовлены выводы по показателям компании."
                else:
                    special_summary = "Задание завершено, но результаты разбора отсутствуют."
        if task.assigned_agent == "content_factory" and (
            task.title or ""
        ).startswith("SEO-ARTICLE:"):
            kind_name = "Статья"
        if by_request.get(task.id):
            try:
                request_data = json.loads(by_request[task.id].content or "{}")
            except (ValueError, TypeError):
                request_data = {}
            if isinstance(request_data, dict) and request_data.get("mode") == "post":
                kind_name = "Пост"
            elif isinstance(request_data, dict) and request_data.get("mode") == "article":
                kind_name = "Статья"
        # 'completed' means worker finished, not approved or published.
        output.append({
            "id": str(task.id),
            "agent": task.assigned_agent,
            "title": task_title(task, by_request.get(task.id)),
            "state": key,
            "label": display_label,
            "tone": "review" if needs_decision else kind,
            "description": explanation,
            "needs_decision": needs_decision,
            "kind_label": kind_name,
            "current_stage": stage_name,
            "display_summary": special_summary,
            "result_count": len(items),
            "artifacts": [{
                "id": str(art.id), "kind": art.kind,
                "title": readable_artifact_title(art),
                "status": getattr(art.status, "value", str(art.status)),
            } for art in items[:15]],
            "animator_stage": animator_stage,
            "animator_calls": animator_calls,
            "stage_signature": "|".join(
                [str(run.id) + ":" + run.status for run in by_runs.get(task.id, [])[-20:]]
                + (["seo:" + latest_seo_stages[task.id][0][0]]
                   if task.id in latest_seo_stages else [])
            ),
            # DB timestamps are UTC; SQLite returns func.now() without tzinfo.
            "created_at": (
                (task.created_at.replace(tzinfo=timezone.utc)
                 if task.created_at.tzinfo is None else
                 task.created_at.astimezone(timezone.utc)).isoformat()
                if task.created_at else None
            ),
        })
    return output


WORK_PAGE_SIZE = 20
WORK_PERIODS = {"all": None, "7": 7, "30": 30, "90": 90}
WORK_TYPES = {
    "all": "Все виды", "post": "Посты", "article": "Статьи",
    "creative": "Креативы", "video": "Видео",
    "plan": "Контент-планы", "edit": "Доработки",
    "seo": "SEO-завод", "business": "Бизнес-анализ",
}


def work_page(db: Session, product_id: UUID, *, tab: str = "all",
              query: str = "", period: str = "all", kind: str = "all",
              page: int = 1,
              page_size: int = WORK_PAGE_SIZE) -> dict:
    """Tenant-scoped SQL filtering/counting before pagination.

    Active, failed and waiting tasks cannot disappear behind a global recent
    limit. Only the currently displayed page is hydrated with artifacts/runs.
    """
    tab = tab if tab in {"all", "active", "approval", "ready", "problems"} else "all"
    period = period if period in WORK_PERIODS else "all"
    q = " ".join(str(query or "").split())[:120]
    kind = kind if kind in WORK_TYPES else "all"
    page = max(1, min(int(page), 100_000))
    page_size = max(1, min(int(page_size), 60))
    base = [Task.product_id == product_id, Task.assigned_agent.in_(WORK_AGENTS)]
    if WORK_PERIODS[period] is not None:
        base.append(Task.created_at >=
                    datetime.now(timezone.utc) - timedelta(days=WORK_PERIODS[period]))
    if kind != "all":
        types = {
            "creative": ("content_creative",),
            "video": ("content_renderer", "whiteboard_renderer"),
            "plan": ("editorial_planner",),
            "edit": ("content_editor",),
            "seo": ("seo_factory",),
            "business": ("business_insights",),
        }
        if kind in types:
            base.append(Task.assigned_agent.in_(types[kind]))
        else:
            req_mode = exists(select(Artifact.id).where(
                Artifact.product_id == product_id,
                Artifact.task_id == Task.id,
                Artifact.kind == "content_request",
                or_(
                    Artifact.content.like(f'%"mode": "{kind}"%'),
                    Artifact.content.like(f'%"mode":"{kind}"%'),
                ),
            ))
            base.append(Task.assigned_agent == "content_factory")
            if kind == "post":
                base.append(or_(req_mode, Task.title.ilike("POST:%")))
            else:
                # SEO-ARTICLE is a real content_factory article job, not an
                # SEO-factory planning job. It belongs in the article filter.
                base.append(or_(req_mode, Task.title.ilike("ARTICLE:%"),
                                Task.title.like("SEO-ARTICLE:%")))
    if q:
        # SQLite lower()/ILIKE is ASCII-only; use our deterministic
        # sg_casefold() on SQLite so mixed-case Russian searches work.
        # Other databases retain their native case-insensitive ILIKE.
        sqlite_search = db.get_bind().dialect.name == "sqlite"
        normalized = q.casefold() if sqlite_search else q
        escaped = normalized.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"

        def text_matches(column):
            if sqlite_search:
                return func.sg_casefold(column).like(pattern, escape="\\")
            return column.ilike(pattern, escape="\\")

        request_match = exists(select(Artifact.id).where(
            Artifact.product_id == product_id,
            Artifact.task_id == Task.id,
            # Planning and business-review tasks carry user topics in their
            # own artifacts, not in content/creative/edit requests.
            Artifact.kind.in_((
                "content_request", "creative_request", "edit_request",
                "seo_factory_request", "business_review",
            )),
            text_matches(Artifact.content),
        ))
        base.append(or_(
            text_matches(Task.title),
            text_matches(Task.description),
            request_match,
        ))
    # The latest draft editorial plan is a real user decision even when the
    # Artifact status field is not awaiting_approval.
    plan_candidates = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.kind == "editorial_plan",
    ).order_by(Artifact.created_at.desc()).limit(12)))
    draft_plans = set()
    for art in plan_candidates:
        try:
            payload = json.loads(art.content or "{}")
            if (isinstance(payload, dict) and payload.get("approval_state") == "draft"
                    and art.task_id):
                draft_plans.add(art.task_id)
                break
        except (TypeError, ValueError):
            continue
    pending = exists(select(Artifact.id).where(
        Artifact.product_id == product_id,
        Artifact.task_id == Task.id,
        Artifact.kind.in_(RESULT_KINDS),
        Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
    ))
    attention_parts = [Task.status.in_((TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED)), pending]
    if draft_plans:
        attention_parts.append(Task.id.in_(draft_plans))
    attention = or_(*attention_parts)
    active = Task.status.in_((TaskStatus.QUEUED, TaskStatus.RUNNING))
    predicates = {
        "all": None,
        "approval": attention,
        "active": active,
        "ready": Task.status == TaskStatus.COMPLETED,
        "problems": Task.status.in_((TaskStatus.FAILED, TaskStatus.CANCELLED)),
    }
    counts = {
        name: int(db.scalar(select(func.count(Task.id)).where(
            *base, *((predicate,) if predicate is not None else ()),
        )) or 0)
        for name, predicate in predicates.items()
    }
    total = counts[tab]
    max_page = max(1, (total + page_size - 1) // page_size)
    page = min(page, max_page)
    stmt = select(Task).where(
        *base, *((predicates[tab],) if predicates[tab] is not None else ()),
    )
    if tab == "all":
        priority = case(
            (attention, 0), (active, 1),
            (Task.status == TaskStatus.CANCELLED, 2),
            else_=3,
        )
        stmt = stmt.order_by(priority, Task.created_at.desc(), Task.id.desc())
    else:
        stmt = stmt.order_by(Task.created_at.desc(), Task.id.desc())
    tasks = list(db.scalars(stmt.limit(page_size).offset((page - 1) * page_size)))
    return {
        "items": _work_items_for_tasks(db, product_id, tasks),
        "total": total, "counts": counts, "page": page,
        "page_size": page_size, "q": q, "tab": tab, "period": period,
        "kind": kind,
    }


def notice_items(db: Session, product_id: UUID, limit: int = 8) -> list[dict]:
    """Actionable notifications: material approval, planner draft, failed job.

    No persistent 'read' flag is implied. These are live tasks awaiting action.
    """
    notes = []
    recent = datetime.now(timezone.utc) - timedelta(days=21)
    def created_stamp(row):
        value = row.created_at
        if value is None:
            return ""
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.isoformat()

    candidates = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
        Artifact.kind.in_(RESULT_KINDS - {"editorial_plan"}),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(limit)))
    for row in candidates:
        is_animator = row.kind == "whiteboard_video"
        readable_title = readable_artifact_title(row)
        resource_kind = ("video" if row.kind in {"whiteboard_video", "video_render"}
                         else "post" if row.kind == "channel_text" else "material")
        notes.append({
            "kind": "review", "text": (
                "Простое видео готово: " if is_animator else
                "Готов MP4: " if row.kind == "video_render" else
                "Материал готов к проверке: "
            ) + readable_title[:95],
            "resource_kind": resource_kind,
            "resource_title": readable_title,
            "resource_status": "Готово · проверить",
            "url": (f"/studio/products/{product_id}/whiteboard/{row.task_id}"
                     if is_animator else f"/studio/products/{product_id}/materials/{row.id}"),
            "id": str(row.id), "created_at": created_stamp(row),
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
                "resource_kind": ("video" if draft.kind in {"whiteboard_video", "video_render"}
                                  else "post" if draft.kind == "channel_text" else "material"),
                "resource_title": readable_artifact_title(draft),
                "resource_status": "Черновик",
                "url": f"/studio/products/{product_id}/materials/{draft.id}",
                "id": str(draft.id), "created_at": created_stamp(draft),
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
                "resource_kind": "plan",
                "resource_title": "Новый контент-план",
                "resource_status": "Ожидает утверждения",
                "url": f"/studio/products/{product_id}/plan?view=draft",
                "id": str(plan.id), "created_at": created_stamp(plan),
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
            "text": "Подтвердите создание простого видео (платные API)",
            "resource_kind": "video",
            "resource_title": "Создание простого видео",
            "resource_status": "Требует подтверждения",
            "url": f"/studio/products/{product_id}/whiteboard/{task.id}",
            "id": str(task.id), "created_at": created_stamp(task),
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
            "kind": "error", "text": (
                "SEO-завод остановился — проверьте планирование"
                if task.assigned_agent == "seo_factory" else
                "Не удалось завершить бизнес-анализ — откройте разбор"
                if task.assigned_agent == "business_insights" else
                "Не удалось подготовить материал — проверьте задание"
            ),
            "resource_kind": "task",
            "resource_title": task_title(task),
            "resource_status": "Ошибка",
            "url": (f"/studio/products/{product_id}/whiteboard/{task.id}"
                     if task.assigned_agent == "whiteboard_renderer"
                     else f"/studio/products/{product_id}/tasks/{task.id}"),
            "id": str(task.id), "created_at": created_stamp(task),
        })
    # Mixed materials, draft plans and failed jobs must be ordered by real
    # creation time, not by which database query produced them.
    notes.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
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
        <div class="sg-notify-head"><strong>Требует внимания</strong>
          <button type="button" class="sg-notify-read" data-sg-read-all
                  data-read-url="/studio/products/{product_id}/notifications/read-all" hidden>Прочитать все</button></div>
        <div data-sg-notify-list role="status" aria-live="polite">Загрузка уведомлений…</div>
        <a class="sg-notify-all" href="/studio/products/{product_id}/work">Все задания →</a>
      </div>
    </details>"""


def work_url(product_id: UUID, item: dict) -> str:
    base = f"/studio/products/{product_id}"
    kind = "whiteboard" if item.get("agent") == "whiteboard_renderer" else "tasks"
    return f"{base}/{kind}/{item['id']}"


def activity_html(product_id: UUID, items: list[dict], tab: str = "all", *,
                  q: str = "", period: str = "all", kind: str = "all",
                  page: int = 1, total: int | None = None, page_size: int = WORK_PAGE_SIZE,
                  counts: dict | None = None) -> str:
    """Native navigation + compact responsive cards; supports server-side pages.

    Keep legacy list calls working (home overview / tests), but render only
    the already filtered/ordered page when 'total' is supplied by SQL.
    """
    base = f"/studio/products/{product_id}"
    paged = total is not None
    tabs = (
        ("approval", "Внимание"), ("active", "В работе"),
        ("ready", "Готово"), ("all", "Все"), ("problems", "Ошибки"),
    )

    def href(next_tab: str, next_page: int = 1, *, partial: bool = False) -> str:
        params = {"tab": next_tab}
        if q:
            params["q"] = q
        if period != "all":
            params["period"] = period
        if kind != "all":
            params["kind"] = kind
        if next_page > 1:
            params["page"] = str(next_page)
        if partial:
            params["partial"] = "1"
        return f"{base}/work?{safe(urlencode(params))}"

    selector = '<nav class="sg-work-tabs" aria-label="Фильтр заданий">' + "".join(
        f'<a href="{href(name)}"'
        f' class="{"active" if name == tab else ""}"'
        f'{" aria-current=page" if name == tab else ""}>'
        f'{label}'
        + (f' <span class="sg-work-tab-count">{int(counts[name])}</span>'
           if counts is not None and name in counts else '')
        + '</a>'
        for name, label in tabs
    ) + '</nav>'
    if paged:
        visible = list(items)
    else:
        visible = [item for item in items if (
            tab == "all" or
            (tab == "active" and item["state"] in {"queued", "running"}) or
            (tab == "approval" and (item.get("needs_decision") or item["state"] in {"waiting_approval", "failed"})) or
            (tab == "ready" and item["state"] == "completed") or
            (tab == "problems" and item["state"] in {"failed", "cancelled"})
        )]
        if tab == "all":
            def order(item):
                if item.get("needs_decision") or item["state"] in {"waiting_approval", "failed"}:
                    return 0
                if item["state"] in {"queued", "running"}:
                    return 1
                if item["state"] == "cancelled":
                    return 2
                return 3
            visible.sort(key=order)

    cards = []
    for item in visible:
        count = item.get("result_count", len(item.get("artifacts") or []))
        state = item["state"]
        summary = item.get("display_summary") or (
            item.get("current_stage") if state == "running" else None
        )
        if item.get("agent") == "whiteboard_renderer":
            stage = ANIMATOR_PHASES.get(str(item.get("animator_stage")))
            if stage:
                summary = stage
        if item.get("needs_decision") and state == "completed":
            summary = "Материал подготовлен — требуется решение"
        if not summary:
            summary = (
                f"Подготовлено материалов: {count}. Публикация отдельно."
                if state == "completed" and count else
                "Задание завершено, но готовый результат не найден."
                if state == "completed" else
                item.get("description") or "Подробности доступны в задании."
            )
        created = ""
        iso = item.get("created_at")
        if iso:
            try:
                created = datetime.fromisoformat(iso).strftime("%d.%m.%Y")
            except (TypeError, ValueError):
                pass
        meta = (f'<time datetime="{safe(iso)}">Создано {safe(created)}</time>'
                if created else "")
        primary = (
            f'<a class="sg-work-item sg-work-{safe(item["tone"])}"'
            f' href="{work_url(product_id, item)}">'
            '<span class="sg-work-item-head">'
            f'<span class="sg-work-kind">{safe(item.get("kind_label") or "Задание")}</span>'
            f'<span class="sg-work-status sg-work-{safe(item["tone"])}">'
            f'{safe(item["label"])}</span></span>'
            f'<span class="sg-work-item-main"><b>{safe(item["title"])}</b>'
            f'<span class="sg-work-summary">{safe(summary)}</span></span>'
            f'<span class="sg-work-item-foot">{meta}'
            '<span class="sg-work-open">Открыть <span aria-hidden="true">→</span></span>'
            '</span></a>'
        )
        cards.append(resource_widget(
            kind="task", primary_html=primary, description="", detail_label=None,
        ))
    messages = {
        "all": "Здесь появятся созданные задания.",
        "active": "Сейчас нет заданий в работе.",
        "approval": "Нет заданий, требующих решения или исправления.",
        "ready": "Готовых заданий пока нет.",
        "problems": "Ошибок в показанных заданиях нет.",
    }
    empty_message = (
        "По выбранным условиям задания не найдены. Измените поиск или фильтры."
        if q or period != "all" or kind != "all" else messages.get(tab, messages["all"])
    )
    empty = (
        '<div class="sg-work-empty" role="status">'
        f'<p>{safe(empty_message)}</p>'
        + (f'<a class="btn primary" href="{base}/create">Создать материал</a>'
           if tab == "all" and not q and period == "all" and kind == "all" and not items else '')
        + '</div>'
    )
    search_form = (
        f'<form class="sg-work-search" action="{base}/work" method="get" role="search">'
        f'<input type="hidden" name="tab" value="{safe(tab)}">'
        '<label class="sg-work-search-label" for="sg-work-q">Поиск задания</label>'
        f'<input type="search" id="sg-work-q" name="q" maxlength="120"'
        f' placeholder="Название или тема" value="{safe(q)}" enterkeyhint="search">'
        '<label class="sg-work-search-label" for="sg-work-period">Период</label>'
        '<select id="sg-work-period" name="period" aria-label="Период заданий">'
        + "".join(
            f'<option value="{value}"{" selected" if period == value else ""}>{label}</option>'
            for value, label in (
                ("all", "За всё время"), ("7", "7 дней"),
                ("30", "30 дней"), ("90", "90 дней"),
            )
        )
        + '</select>'
        + '<label class="sg-work-search-label" for="sg-work-kind">Вид задания</label>'
        + '<select id="sg-work-kind" name="kind" aria-label="Вид задания">'
        + "".join(
            f'<option value="{value}"{" selected" if kind == value else ""}>{label}</option>'
            for value, label in WORK_TYPES.items()
        )
        + '</select><button class="btn" type="submit">Найти</button></form>'
    )
    page_count = max(1, ((total or 0) + page_size - 1) // page_size) if paged else 1
    start_at = (page - 1) * page_size + 1 if paged and total else 0
    end_at = min(page * page_size, total) if paged and total else 0
    pagination = ""
    if paged:
        links = ""
        if page > 1:
            links += f'<a rel="prev" href="{href(tab, page - 1)}">← Назад</a>'
        links += f'<span>Страница {page} из {page_count}</span>'
        if page < page_count:
            links += f'<a rel="next" href="{href(tab, page + 1)}">Вперёд →</a>'
        pagination = (
            '<nav class="sg-work-pagination" aria-label="Страницы заданий">'
            f'<span class="sg-work-page-summary">Показано {start_at}–{end_at} из {total}</span>'
            f'<div class="sg-work-page-links">{links}</div></nav>'
        )
    payload = {
        "jobs": visible,
        "count": total if paged else len(visible),
        "counts": counts or {},
    }
    signature = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:20]
    refresh = f' data-sg-refresh-url="{href(tab, page, partial=True)}"' if paged else ""
    return (
        f'<section class="sg-surface sg-work-board" aria-label="Список заданий"'
        f' data-sg-work-board data-sg-list-signature="{signature}"{refresh}>'
        '<div class="sg-work-toolbar">'
        '<p>Недавние задания. Активные и требующие внимания — в приоритете.</p>'
        f'<a class="btn primary" href="{base}/create">＋ Создать</a></div>'
        + search_form + selector
        + '<button type="button" class="sg-work-update" data-sg-work-update hidden>'
        'Есть обновления. Показать</button>'
        + '<div class="sg-work-list">'
        + ("".join(cards) or empty) + '</div>'
        + pagination + '</section>'
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
        primary = (
            f'<a class="sg-work-item sg-work-{item["tone"]}" href="{work_url(product_id, item)}">'
            f'<div class="sg-work-item-main"><b>{safe(item["title"])}</b>'
            f'<p>{safe(item.get("display_summary") or item.get("current_stage") or (ANIMATOR_PHASES.get(str(item.get("animator_stage")), item["description"]) if item.get("agent") == "whiteboard_renderer" else item["description"]))}</p></div>'
            f'<span class="sg-work-status sg-work-{item["tone"]}">{safe(item["label"])}</span>'
            '<span class="sg-work-arrow" aria-hidden="true">→</span></a>'
        )
        cards.append(resource_widget(
            kind="task", primary_html=primary,
            description=item["description"],
            detail_label="Сведения о задании",
        ))
    return (
        '<section class="sg-surface sg-overview-work" data-sg-work-overview>'
        '<div class="sg-section-head"><div><h2>' +
        ("Сейчас создаётся" if working else "Последние задания") +
        '</h2><p>Задания не пропадут: вернуться можно в любой момент.</p></div>'
        f'<a class="sg-mini-link" href="{base}/work">Все задания →</a></div>'
        + "".join(cards) + '</section>'
    )


def seo_factory_task_html(product_id: UUID, task: Task, report: dict) -> str:
    """Owner-facing report for exactly one factory task, no approval/publish actions.

    SEO factory completion is not article generation or publication; those are
    tracked by separate content_factory tasks and Publication records.
    """
    base = f"/studio/products/{product_id}"
    state = task_state(task)
    labels = {
        "queued": "В очереди", "running": "Планирование выполняется",
        "completed": "Планирование завершено", "failed": "Ошибка планирования",
        "cancelled": "Планирование отменено", "waiting_approval": "Ожидает решения",
    }
    tones = {
        "queued": "queued", "running": "running", "completed": "done",
        "failed": "error", "cancelled": "error", "waiting_approval": "review",
    }
    stages = []
    if report.get("semantic_ready"):
        stages.append("Семантическое ядро подготовлено")
    if report.get("plan_ready"):
        stages.append("Контент-план сформирован")
    queued = report.get("articles_queued")
    if type(queued) is int and queued >= 0:
        stages.append(f"Статей поставлено в очередь: {queued}")
    deferred = report.get("articles_deferred")
    if type(deferred) is int and deferred > 0:
        stages.append(f"Отложено тем: {deferred}")
    if report.get("landing_ready"):
        total = report.get("landing_candidates_total")
        stages.append(
            f"Вариантов посадочных страниц: {total}"
            if type(total) is int else
            "Предложения посадочных страниц подготовлены"
        )
    raw_phase = report.get("current_stage")
    phase = SEO_PHASE_NAMES.get(str(raw_phase), "") if raw_phase else ""
    # Work-page polling returns a friendly current_stage for running SEO tasks.
    # Keep the same value in the detail so a changed stage is detected without
    # inventing progress percentages or extra paid calls.
    stage_marker = (
        SEO_PHASE_NAMES.get(str(raw_phase), "Обработка SEO-запросов")
        if raw_phase else "Подготовка поисковых запросов и плана"
    ) if state == "running" else ""
    stage_attrs = (
        f' data-sg-stage-mode="seo" data-sg-stage="{safe(stage_marker)}"'
        if state == "running" else ""
    )
    if state == "completed":
        introduction = (
            "Планирование завершено. Это не означает, что статьи написаны "
            "или опубликованы: их создание и публикация выполняются отдельно."
        )
    elif state == "failed":
        introduction = (
            "Планирование остановилось с ошибкой. Готовые этапы сохранены; "
            "проверьте состояние и доступные действия в SEO-заводе."
        )
    elif state == "running":
        introduction = "Идёт подготовка запросов и контент-плана."
    elif state == "queued":
        introduction = "SEO-завод ожидает начала планирования."
    else:
        introduction = "Подробности планирования находятся в SEO-заводе."
    current = (
        '<p class="sg-seo-task-stage"><b>Текущий этап:</b> '
        + safe(phase) + '</p>'
        if phase and state in {"running", "queued", "failed"} else ''
    )
    failure_causes = {
        "upstream_timeout": "Сервис не ответил вовремя",
        "network_failure": "Ошибка соединения с сервисом",
        "provider_http_error": "Сервис вернул ошибку",
        "model_unusable_response": "ИИ-модель вернула неподходящий ответ",
        "plan_validation_failed": "Не удалось проверить контент-план",
        "invalid_data_or_configuration": "Недостаточно корректных данных или настроек",
        "search_upstream_failures": "Поисковые сервисы недоступны",
        "search_invalid_responses": "Поисковые сервисы вернули неподходящие данные",
        "internal_exception": "Внутренняя ошибка выполнения",
    }
    reason = failure_causes.get(str(report.get("last_error_code")), "")
    error_note = (
        f'<p class="sg-seo-task-error" role="alert"><b>Причина:</b> {safe(reason)}</p>'
        if state == "failed" and reason else ""
    )
    completed = (
        '<section class="sg-work-results"><h3>Подтверждённые результаты</h3><ul>'
        + ''.join(f'<li>{safe(value)}</li>' for value in stages)
        + '</ul></section>'
        if stages else
        '<p class="sg-seo-task-muted">Подтверждённых результатов планирования пока нет.</p>'
    )
    return (
        f'<section class="sg-surface sg-work-detail sg-seo-task-detail"'
        f' data-sg-task="{task.id}" data-sg-state="{safe(state)}"{stage_attrs}>'
        f'<span class="sg-work-status sg-work-{safe(tones.get(state, "queued"))}">'
        f'{safe(labels.get(state, "Проверить статус"))}</span>'
        '<h2>SEO-завод: запросы и контент-план</h2>'
        f'<p>{safe(introduction)}</p>'
        + current + error_note + completed
        + '<div class="sg-seo-task-actions">'
        + f'<a class="btn primary" href="{base}/seo">Открыть SEO-завод</a>'
        + f'<a class="sg-mini-link" href="{base}/work">← Все задания</a>'
        + '</div></section>'
    )


def task_detail_html(product_id: UUID, task: Task, runs: list[AgentRun],
                     results: list[Artifact], request: Artifact | None = None) -> str:
    base = f"/studio/products/{product_id}"
    key = task_state(task)
    label, tone, description = STATES.get(key, ("Проверить", "queued", ""))
    seo_proposal = is_seo_publish_proposal(task)
    if seo_proposal and key == "waiting_approval":
        label = "Предложение на согласовании"
    count = len(results)
    steps = []
    if not runs:
        if seo_proposal:
            steps.append("<li>Предложение сформировано. Этапы создания статьи здесь не выполнялись.</li>")
        elif key == "waiting_approval":
            steps.append("<li>Ожидается решение о запуске или продолжении задания.</li>")
        else:
            steps.append("<li>Ожидаем запуска обработки</li>" if key == "queued" else
                         "<li>Обработка запущена. Подробные этапы ещё не поступили.</li>")
    latest_completed = {run.agent_name for run in runs if run.status == "completed"}
    for run in runs[-16:]:
        stage = STAGE_NAMES.get(run.agent_name, "Подготовка материала")
        state = {
            "queued": "Ожидает", "running": "Выполняется",
            "completed": "Готово", "failed": "Ошибка",
        }.get(run.status, "Проверяется")
        if run.status == "failed" and run.agent_name in latest_completed:
            state = "Исправлено при продолжении"
        steps.append(f'<li><span>{safe(stage)}</span><b>{safe(state)}</b></li>')
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
    elif seo_proposal and key == "waiting_approval":
        explanation = ("ИИ-команда предложила публикацию SEO-статьи. Это не готовый материал "
                       "и не разрешение на публикацию. Проверьте реальную статью и её статус "
                       "в SEO-заводе; из этой карточки ничего не публикуется.")
    elif key == "waiting_approval" and task.assigned_agent == "whiteboard_renderer":
        explanation = "Нужно подтвердить создание видео. Платные вызовы требуют отдельного согласия."
    else:
        explanation = description
    refresh = ('<p class="sg-work-refresh" role="status">Состояние обновляется каждые 15 секунд. '
               'Эту страницу можно закрыть и вернуться позже.</p>'
               if key in {"queued", "running"} else "")
    creative_resume = task.assigned_agent == "content_creative"
    fail = (
        f'<form action="{base}/tasks/{task.id}/retry" method="post">'
        '<button class="btn secondary" type="submit">'
        + ("Продолжить с сохранённых этапов" if creative_resume and results
           else "Повторить задание")
        + '</button></form>'
        if key in {"failed", "cancelled"} and task.assigned_agent
        in {"content_factory", "content_creative", "content_editor"}
        and (creative_resume or not results) else ""
    )
    empty_result = ("<p>Готовая статья к этому предложению не прикреплена. "
                    "Проверьте статьи и их статус в SEO-заводе.</p>" if seo_proposal
                    else "<p>Результаты появятся здесь после подготовки.</p>")
    next_action = (f'<p><a class="btn secondary" href="{base}/seo">Открыть SEO-завод</a></p>'
                   if seo_proposal else "")
    stage_sig = "|".join(str(run.id) + ":" + run.status for run in runs[-20:])
    # Prioritize the output when it exists, and avoid a long process log
    # pushing the useful material down the screen.
    if key == "completed" and count:
        stages_html = (
            '<details class="sg-work-steps sg-work-steps-collapsible">'
            '<summary>Этапы создания</summary><ol>'
            + "".join(steps) + '</ol></details>'
        )
    else:
        stages_html = (
            '<div class="sg-work-steps"><h3>Этапы создания</h3><ol>'
            + "".join(steps) + '</ol></div>'
        )
    results_html = (
        '<div class="sg-work-results"><h3>Результаты</h3>'
        + ("".join(rendered) or empty_result) + '</div>'
    )
    output_first = key in {"completed", "waiting_approval"} and bool(count)
    sections = (
        results_html + stages_html if output_first
        else stages_html + results_html
    )
    return (
        f'<section class="sg-surface sg-work-detail" data-sg-task="{task.id}" data-sg-state="{key}" data-sg-stage="{safe(stage_sig)}">'
        f'<span class="sg-work-status sg-work-{tone}">'
        f'{safe(label)}</span><h2>{safe(task_title(task, request))}</h2><p>{safe(explanation)}</p>'
        + fail + sections + refresh + next_action
        + f'<p><a class="sg-mini-link" href="{base}/work">← Все задания</a></p></section>'
    )
