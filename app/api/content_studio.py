"""Owner-facing visual content workspace for AI Growth OS.

The public production entrypoint is protected by the web edge. This module keeps
publication manual and preserves existing editorial approval guards.
"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.content_studio_design import STUDIO_CSS, STUDIO_JS
from app.content.calendar import PLATFORMS, approve_plan, pause_plan
from app.content.service import queue_content
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Artifact, Product, Task

router = APIRouter()

MATERIAL_KINDS = {
    "article", "channel_text", "video_script", "content_distribution",
    "dzen_brief", "telegram_brief", "short_video_brief",
    "story_brief", "static_image_brief",
}

STATUS_LABELS = {
    "draft": "Черновик",
    "active": "Активен",
    "paused": "На паузе",
    "superseded": "Заменён",
    "planned": "Запланировано",
    "queued": "В очереди",
    "preparing": "Готовится",
    "running": "Готовится",
    "ready_for_review": "Готово к проверке",
    "completed": "Готово",
    "failed": "Ошибка",
    "missed": "Пропущено",
    "cancelled": "Отменено",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def parse_json(record: Artifact) -> dict:
    try:
        value = json.loads(record.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def latest_plan(db: Session, product_id: UUID):
    rows = list(db.scalars(
        select(Artifact)
        .where(Artifact.product_id == product_id, Artifact.kind == "editorial_plan")
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(20)
    ))
    parsed = [(row, parse_json(row)) for row in rows]
    parsed = [(row, value) for row, value in parsed if value]
    parsed.sort(key=lambda item: item[1].get("generated_at", ""), reverse=True)
    return parsed[0] if parsed else (None, None)


def platform_label(key: str | None) -> str:
    if not key:
        return "Площадка"
    return PLATFORMS.get(key, (key.replace("_", " ").title(), ""))[0]


def platform_short(key: str | None) -> str:
    if not key:
        return "—"
    return {
        "own_site": "SEO",
        "dzen": "Дзен",
        "telegram": "TG",
        "youtube_shorts": "YT",
        "instagram_reels": "IG",
        "vk_clips": "VK",
        "vc_ru": "VC",
        "tenchat": "TC",
    }.get(key, key[:3].upper())


def status_label(value: str | None) -> str:
    return STATUS_LABELS.get(value or "", (value or "—").replace("_", " "))


def status_class(value: str | None) -> str:
    return (value or "planned").replace(" ", "_")


def parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def fmt_dt(value: str | None, *, date_only: bool = False) -> str:
    dt = parse_dt(value)
    if not dt:
        return esc(value or "—")
    months = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    if date_only:
        return f"{dt.day} {months[dt.month - 1]}"
    return f"{dt.day} {months[dt.month - 1]}, {dt:%H:%M}"


def day_label(value: str | None) -> tuple[str, str]:
    dt = parse_dt(value)
    if not dt:
        return "Дата", "—"
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    months = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    return weekdays[dt.weekday()], f"{dt.day} {months[dt.month - 1]}"


def state_for_group(group: list[dict]) -> str:
    states = {str(slot.get("state") or "planned") for slot in group}
    if "failed" in states:
        return "failed"
    if "preparing" in states or "running" in states or "queued" in states:
        return "preparing"
    if states and states <= {"ready_for_review"}:
        return "ready_for_review"
    if "ready_for_review" in states:
        return "ready_for_review"
    if "missed" in states:
        return "missed"
    return "planned"


def artifact_kind(record: Artifact) -> str:
    if record.kind == "article":
        return "article"
    if record.kind in {"channel_text", "dzen_brief", "telegram_brief"}:
        return "post"
    if record.kind in {"video_script", "short_video_brief"}:
        return "video"
    return "other"


def artifact_kind_label(record: Artifact, value: dict | None = None) -> str:
    value = value or parse_json(record)
    if record.kind == "article":
        return "Статья"
    if record.kind == "channel_text":
        return platform_label(value.get("channel", "")) or "Пост"
    if record.kind in {"video_script", "short_video_brief"}:
        platform = (value.get("production_brief") or value).get("target_platform")
        return f"Сценарий · {platform_label(platform)}" if platform else "Сценарий"
    if record.kind == "dzen_brief":
        return "Дзен"
    if record.kind == "telegram_brief":
        return "Telegram"
    if record.kind == "story_brief":
        return "Stories"
    if record.kind == "static_image_brief":
        return "Визуал"
    return record.kind.replace("_", " ").title()


def artifact_text(record: Artifact, value: dict | None = None) -> str:
    value = value or parse_json(record)
    if not value:
        return record.content
    if record.kind == "article":
        return str(value.get("full_article") or value.get("reader_article") or value.get("body") or record.content)
    if record.kind == "channel_text":
        return str(value.get("body") or value.get("text") or record.content)
    if record.kind in {"video_script", "short_video_brief"}:
        brief = value.get("production_brief") or value
        return str(brief.get("voiceover_or_dialogue_script") or value.get("script") or record.content)
    for key in ("body", "text", "copy", "description", "content"):
        if value.get(key):
            return str(value[key])
    return json.dumps(value, ensure_ascii=False, indent=2)


def excerpt(text: str, limit: int = 120) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[: limit - 1] + "…"


def visual_class(index: int) -> str:
    return ("", "v2", "v3", "v4")[index % 4]


def brand_html() -> str:
    return (
        '<a class="brand" href="/studio">'
        '<span class="brand-mark">S</span>'
        '<span><span class="brand-name">ServGo</span>'
        '<span class="brand-sub">Больше порядка в вашем сервисе</span></span>'
        '</a>'
    )


def sidebar_html() -> str:
    return f"""
    <aside class="sidebar">
      {brand_html()}
      <nav class="nav">
        <a class="active" href="#overview"><span class="nav-ico">⌂</span>Обзор</a>
        <a href="#plan"><span class="nav-ico">▣</span>Контент-план</a>
        <a href="#materials"><span class="nav-ico">▤</span>Материалы</a>
        <a href="#calendar"><span class="nav-ico">□</span>Календарь</a>
        <a href="#analytics"><span class="nav-ico">⌁</span>Аналитика</a>
      </nav>
      <div class="nav-label">Инструменты</div>
      <nav class="nav">
        <a href="#materials"><span class="nav-ico">▱</span>Библиотека</a>
        <a href="#plan"><span class="nav-ico">✦</span>Идеи контента</a>
        <a href="/studio?choose=1"><span class="nav-ico">⇄</span>Проекты</a>
      </nav>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-promo">
        <span>👑</span><b>Growth Studio</b>
        <p>План, материалы и сценарии в одном рабочем пространстве ServGo.</p>
      </div>
      <div class="sidebar-foot">AI Growth OS · owner workspace</div>
    </aside>
    """


def mobile_nav_html() -> str:
    return """
    <nav class="mobile-nav">
      <a class="active" href="#overview"><span class="m-ico">⌂</span>Обзор</a>
      <a href="#plan"><span class="m-ico">▣</span>План</a>
      <a href="#materials"><span class="m-ico">▤</span>Материалы</a>
      <a href="#calendar"><span class="m-ico">□</span>Календарь</a>
    </nav>
    """


def asset_dialog(record: Artifact) -> str:
    value = parse_json(record)
    title = esc(record.title)
    kind_label = esc(artifact_kind_label(record, value))
    copy_id = f"copy-{record.id}"

    if record.kind in {"video_script", "short_video_brief"}:
        brief = value.get("production_brief") or value
        hooks = "".join(f"<li>{esc(x)}</li>" for x in (brief.get("hook_variants") or []))
        shots = []
        for shot in brief.get("shots") or []:
            shots.append(
                '<div class="story-shot">'
                '<div class="story-shot-head">'
                f'<time>{esc(shot.get("start_second"))}–{esc(shot.get("end_second"))} сек.</time>'
                f'<b>{esc(shot.get("purpose") or shot.get("shot_type") or "Сцена")}</b>'
                '</div>'
                f'<p><b>Кадр:</b> {esc(shot.get("visual_description"))}</p>'
                f'<p><b>Камера:</b> {esc(shot.get("camera_and_movement"))}</p>'
                f'<p><b>Действие:</b> {esc(shot.get("action"))}</p>'
                f'<p><b>На экране:</b> {esc(shot.get("on_screen_text"))}</p>'
                f'<p><b>Переход:</b> {esc(shot.get("transition"))}</p>'
                f'<div class="story-prompt"><b>Промпт для будущей генерации:</b><br>{esc(shot.get("generation_prompt"))}</div>'
                '</div>'
            )
        shots_html = "".join(shots) or '<div class="empty">Раскадровка ещё не заполнена.</div>'
        body = (
            f'<div id="{copy_id}" class="article-text">'
            f'<b>Площадка:</b> {esc(platform_label(brief.get("target_platform")))}\n'
            f'<b>Цель:</b> {esc(brief.get("objective"))}\n\n'
            f'<b>Хуки:</b><ul>{hooks}</ul>'
            f'<b>Озвучка / диалог:</b>\n{esc(brief.get("voiceover_or_dialogue_script"))}\n\n'
            f'<b>Монтаж и сведение:</b> {esc(brief.get("editing_mix_notes") or brief.get("editing_rhythm"))}'
            '</div>'
            f'<div class="storyboard-grid" style="margin-top:16px">{shots_html}</div>'
        )
    else:
        body = f'<div id="{copy_id}" class="article-text">{esc(artifact_text(record, value))}</div>'

    return f"""
    <dialog class="dialog" id="material-{record.id}">
      <div class="dialog-head">
        <div><span class="status draft">{kind_label}</span><h3>{title}</h3></div>
        <button class="dialog-close" type="button" data-close-dialog aria-label="Закрыть">×</button>
      </div>
      <div class="dialog-body">{body}</div>
      <div class="dialog-actions">
        <button class="btn ghost" type="button" data-copy="{copy_id}">Копировать</button>
        <button class="btn primary" type="button" data-close-dialog>Готово</button>
      </div>
    </dialog>
    """


def material_card(record: Artifact) -> str:
    value = parse_json(record)
    kind = artifact_kind(record)
    title = esc(record.title)
    label = esc(artifact_kind_label(record, value))
    text = artifact_text(record, value)
    meta = excerpt(text, 90)
    created = record.created_at.strftime("%d.%m") if record.created_at else ""
    return (
        f'<button type="button" class="material-card" data-material data-kind="{kind}" '
        f'data-search="{esc(record.title + " " + label + " " + meta)}" '
        f'data-open-dialog="material-{record.id}">'
        f'<div class="material-cover {esc(record.kind)}"><span class="material-kind">{label}</span></div>'
        '<div class="material-body">'
        f'<h4>{title}</h4><p>{esc(meta)}</p>'
        f'<div class="package-foot"><span>{esc(created)}</span><span>Открыть →</span></div>'
        '</div></button>'
    )


def package_card(
    *,
    product: Product,
    plan_row: Artifact | None,
    plan: dict | None,
    key: str,
    group: list[dict],
    package_artifacts: list[Artifact],
    index: int,
) -> str:
    topic = group[0].get("topic") or key
    state = state_for_group(group)
    task_ids = {slot.get("task_id") for slot in group if slot.get("task_id")}
    platforms = []
    for slot in group:
        platform = slot.get("platform") or ""
        if platform and platform not in platforms:
            platforms.append(platform)
    chips = "".join(
        f'<span class="chip blue">{esc(platform_short(p))} · {esc(platform_label(p))}</span>'
        for p in platforms
    )
    first_publish = min((slot.get("publish_at") or "" for slot in group), default="")
    objective = group[0].get("objective") or group[0].get("audience") or "Контент-пакет для проверки гипотезы"
    queue_button = ""
    if plan_row and plan and plan.get("approval_state") == "active" and not task_ids:
        queue_button = (
            f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/packages/{esc(key)}/queue">'
            '<button class="btn secondary small" type="submit">Подготовить сейчас</button></form>'
        )
    first_asset = package_artifacts[0] if package_artifacts else None
    open_button = (
        f'<button class="btn ghost small" type="button" data-open-dialog="material-{first_asset.id}">Материалы · {len(package_artifacts)}</button>'
        if first_asset else '<span class="chip">Материалы ещё не готовы</span>'
    )
    return f"""
    <article class="package-card">
      <div class="package-visual {visual_class(index)}">
        <span class="package-orb one"></span><span class="package-orb two"></span>
        <span class="package-symbol">{index + 1}</span>
        <span class="package-state status {status_class(state)}">{esc(status_label(state))}</span>
      </div>
      <div class="package-body">
        <h3>{esc(topic)}</h3>
        <div class="package-objective">{esc(objective)}</div>
        <div class="chips">{chips}</div>
        <div class="package-foot">
          <span>□ {fmt_dt(first_publish)}</span>
          <span>{len(group)} публикац.</span>
        </div>
        <div class="preview-actions">{open_button}{queue_button}</div>
      </div>
    </article>
    """



def mobile_logo_html() -> str:
    return """
    <div class="m-brand">
      <span class="m-logo-mark">S</span>
      <span class="m-logo-word">ServGo</span>
      <span class="m-logo-actions"><span class="m-bell">○</span><span class="m-avatar">S</span></span>
    </div>
    """


def mobile_calendar_html(slots: list[dict]) -> str:
    ordered = sorted(slots, key=lambda item: item.get("publish_at") or "")
    by_day: dict[str, list[dict]] = defaultdict(list)
    for slot in ordered:
        dt = parse_dt(slot.get("publish_at"))
        key = dt.date().isoformat() if dt else slot.get("publish_at") or "unknown"
        by_day[key].append(slot)
    days = []
    for index, (_, day_slots) in enumerate(list(by_day.items())[:5]):
        weekday, date_text = day_label(day_slots[0].get("publish_at"))
        days.append(
            f'<button class="m-day{" active" if index == 0 else ""}" type="button">'
            f'<b>{esc(weekday)}</b><span>{esc(date_text)}</span><i></i></button>'
        )
    timeline = []
    for slot in ordered[:5]:
        dt = parse_dt(slot.get("publish_at"))
        time_text = dt.strftime("%H:%M") if dt else "—"
        timeline.append(
            '<div class="m-event">'
            f'<time>{esc(time_text)}</time>'
            f'<span class="m-platform">{esc(platform_short(slot.get("platform") or ""))}</span>'
            '<div class="m-event-copy">'
            f'<b>{esc(slot.get("topic"))}</b>'
            f'<small>{esc(platform_label(slot.get("platform") or ""))}</small>'
            '</div><span class="m-more">⋮</span></div>'
        )
    return (
        '<section class="m-card m-calendar" id="m-calendar">'
        '<div class="m-section-head"><div><span class="m-section-icon">▱</span><b>Контент-календарь</b></div><span>›</span></div>'
        f'<div class="m-days">{"".join(days)}</div>'
        f'<div class="m-timeline">{"".join(timeline) or "<div class=\\"empty\\">Публикаций пока нет.</div>"}</div>'
        '<a class="m-calendar-open" href="#calendar"><span>□</span> Открыть календарь <b>→</b></a>'
        '</section>'
    )


def mobile_package_card(
    *,
    product: Product,
    plan_row: Artifact | None,
    plan: dict | None,
    key: str,
    group: list[dict],
    package_artifacts: list[Artifact],
    index: int,
) -> str:
    topic = group[0].get("topic") or key
    state = state_for_group(group)
    platforms = []
    for slot in group:
        p = slot.get("platform") or ""
        if p and p not in platforms:
            platforms.append(p)
    chips = "".join(f'<span>{esc(platform_short(p))}</span>' for p in platforms[:5])
    first_publish = min((slot.get("publish_at") or "" for slot in group), default="")
    first_asset = package_artifacts[0] if package_artifacts else None
    open_action = (
        f'data-open-dialog="material-{first_asset.id}"' if first_asset else ""
    )
    return (
        f'<article class="m-package m-scene-{index % 4}" {open_action}>'
        f'<div class="m-package-cover"><span class="m-package-status {status_class(state)}">{esc(status_label(state))}</span></div>'
        '<div class="m-package-body">'
        f'<h3>{esc(topic)}</h3>'
        f'<p>{esc(group[0].get("objective") or "Материалы пакета для нескольких площадок")}</p>'
        f'<div class="m-channel-row">{chips}<em>{len(package_artifacts)} материала</em></div>'
        f'<div class="m-package-date">□ {fmt_dt(first_publish)}</div>'
        '</div></article>'
    )


def mobile_material_card(record: Artifact) -> str:
    value = parse_json(record)
    kind = artifact_kind(record)
    return (
        f'<button class="m-material {kind}" type="button" data-open-dialog="material-{record.id}">'
        f'<span class="m-material-type">{esc(artifact_kind_label(record, value))}</span>'
        f'<span class="m-material-thumb">{("▧" if kind == "article" else "▶" if kind == "video" else "✦")}</span>'
        f'<b>{esc(record.title)}</b>'
        f'<small>{esc(excerpt(artifact_text(record, value), 55))}</small>'
        '</button>'
    )


def mobile_dashboard_html(
    *,
    product: Product,
    plan_row: Artifact | None,
    plan: dict | None,
    slots: list[dict],
    groups: dict[str, list[dict]],
    by_task: dict[str, list[Artifact]],
    material_records: list[Artifact],
    action_html: str,
    horizon: int,
    channels: list[str],
) -> str:
    package_cards = []
    for index, (key, group) in enumerate(sorted(
        groups.items(),
        key=lambda item: min((s.get("publish_at") or "" for s in item[1]), default="")
    )):
        task_ids = {slot.get("task_id") for slot in group if slot.get("task_id")}
        package_artifacts: list[Artifact] = []
        for task_id in task_ids:
            package_artifacts.extend([a for a in by_task.get(str(task_id), []) if a.kind in MATERIAL_KINDS])
        package_cards.append(mobile_package_card(
            product=product,
            plan_row=plan_row,
            plan=plan,
            key=key,
            group=group,
            package_artifacts=package_artifacts,
            index=index,
        ))
    materials = "".join(mobile_material_card(record) for record in material_records[:6])
    return f"""
    <div class="mobile-only-dashboard">
      {mobile_logo_html()}
      <header class="m-hero">
        <div class="m-title-row"><h1>Growth Studio</h1><span class="beta">BETA</span></div>
        <p>Готовые идеи, тексты и медиаконтент для роста ваших бронирований</p>
      </header>

      <section class="m-kpis">
        <div class="m-kpi blue"><span class="m-kpi-icon">▱</span><strong>{len(groups)}<small>пакетов</small></strong><p>запланировано<br>на период</p></div>
        <div class="m-kpi green"><span class="m-kpi-icon">□</span><strong>{esc(horizon)}<small>дней</small></strong><p>активный<br>контент-план</p></div>
        <div class="m-kpi purple"><span class="m-kpi-icon">⌘</span><strong>{len(channels)}<small>площадок</small></strong><div class="m-mini-platforms">{"".join(f"<span>{esc(platform_short(x))}</span>" for x in channels[:4])}</div></div>
      </section>

      {mobile_calendar_html(slots)}

      <section class="m-card m-packages" id="m-packages">
        <div class="m-section-head"><div><span class="m-section-icon">▱</span><b>Контент-пакеты</b></div><span class="m-all-pill">Все</span></div>
        <div class="m-package-actions">{action_html}</div>
        <div class="m-package-carousel">{"".join(package_cards) or '<div class="empty">Контент-пакеты ещё не сформированы.</div>'}</div>
        <div class="m-dots"><i class="active"></i><i></i><i></i></div>
      </section>

      <section class="m-card m-materials" id="m-materials">
        <div class="m-section-head"><div><span class="m-section-icon">▤</span><b>Подготовленные материалы</b></div><a href="#materials">Все</a></div>
        <div class="m-material-strip">{materials or '<div class="empty">Готовых материалов пока нет.</div>'}</div>
      </section>
    </div>
    """;


def selected_preview(record: Artifact | None) -> str:
    if record is None:
        return (
            '<section class="panel preview"><div class="preview-empty">'
            '<div class="brand-mark" style="margin:0 auto 14px">S</div>'
            '<b>Материалы появятся здесь</b><p>После подготовки пакета можно будет быстро открыть статью, пост или раскадровку.</p>'
            '</div></section>'
        )
    value = parse_json(record)
    text = artifact_text(record, value)
    return f"""
    <section class="panel preview">
      <div class="preview-hero"><span class="brand-mark">S</span></div>
      <div class="preview-content">
        <div class="preview-kicker">
          <span class="chip blue">{esc(artifact_kind_label(record, value))}</span>
          <span class="status ready_for_review">Предпросмотр</span>
        </div>
        <h3>{esc(record.title)}</h3>
        <div class="preview-copy">{esc(excerpt(text, 900))}</div>
        <div class="preview-actions">
          <button class="btn primary" type="button" data-open-dialog="material-{record.id}">Открыть полностью</button>
        </div>
      </div>
    </section>
    """


def product_dashboard(db: Session, product: Product) -> str:
    plan_row, plan = latest_plan(db, product.id)
    tasks = list(db.scalars(
        select(Task)
        .where(Task.product_id == product.id, Task.assigned_agent.in_(["content_factory", "editorial_planner"]))
        .order_by(Task.created_at.desc())
        .limit(150)
    ))
    artifacts = list(db.scalars(
        select(Artifact)
        .where(Artifact.product_id == product.id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(700)
    ))

    by_task: dict[str, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_id:
            by_task[str(artifact.task_id)].append(artifact)

    slots = (plan or {}).get("slots") or []
    groups: dict[str, list[dict]] = defaultdict(list)
    for slot in slots:
        groups[slot.get("package_key") or "unknown"].append(slot)

    content_tasks = [task for task in tasks if task.assigned_agent == "content_factory"]
    material_records = [
        artifact for artifact in artifacts
        if artifact.kind in MATERIAL_KINDS and artifact.task_id
    ]
    # Deduplicate artifacts by id while retaining newest-first order.
    seen = set()
    material_records = [a for a in material_records if not (a.id in seen or seen.add(a.id))]

    ready = sum(1 for slot in slots if slot.get("state") == "ready_for_review")
    failed = sum(1 for slot in slots if slot.get("state") == "failed")
    channels = sorted({slot.get("platform") for slot in slots if slot.get("platform")})
    total_slots = len(slots)
    done_like = sum(1 for slot in slots if slot.get("state") in {"ready_for_review", "completed"})
    progress = min(100, round((done_like / total_slots) * 100)) if total_slots else 0
    horizon = (plan or {}).get("horizon_days") or 14

    action_html = ""
    if plan_row and plan:
        if plan.get("approval_state") == "draft":
            action_html = (
                f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/approve">'
                '<button class="btn primary" type="submit">✓ Подтвердить план</button></form>'
            )
        elif plan.get("approval_state") == "active":
            action_html = (
                f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/pause">'
                '<button class="btn ghost" type="submit">Ⅱ Пауза</button></form>'
            )

    kpis = f"""
    <section class="kpis" id="overview">
      <div class="kpi blue"><span class="kpi-icon">▱</span><div><strong>{len(groups)}</strong><small>контент-пакетов<br>в текущем цикле</small></div></div>
      <div class="kpi green"><span class="kpi-icon">□</span><div><strong>{esc(horizon)}</strong><small>дней<br>горизонт плана</small></div></div>
      <div class="kpi purple"><span class="kpi-icon">⌘</span><div><strong>{len(channels)}</strong><small>площадок<br>в контент-плане</small></div></div>
      <div class="kpi orange"><span class="kpi-icon">✓</span><div><strong>{ready}</strong><small>материалов<br>готово к проверке</small></div></div>
    </section>
    """

    by_day: dict[str, list[dict]] = defaultdict(list)
    for slot in sorted(slots, key=lambda item: item.get("publish_at") or ""):
        dt = parse_dt(slot.get("publish_at"))
        key = dt.date().isoformat() if dt else slot.get("publish_at") or "unknown"
        by_day[key].append(slot)
    day_cards = []
    for _, day_slots in by_day.items():
        weekday, date_text = day_label(day_slots[0].get("publish_at"))
        slot_html = []
        for slot in day_slots:
            dt = parse_dt(slot.get("publish_at"))
            time_text = dt.strftime("%H:%M") if dt else "—"
            slot_html.append(
                '<div class="slot">'
                '<div class="slot-top">'
                f'<span class="slot-time">{esc(time_text)}</span>'
                f'<span class="platform-mini">{esc(platform_short(slot.get("platform") or ""))}</span>'
                '</div>'
                f'<div class="slot-title">{esc(slot.get("topic"))}</div>'
                f'<div class="slot-meta">{esc(platform_label(slot.get("platform") or ""))} · {esc(status_label(slot.get("state")))}</div>'
                '</div>'
            )
        day_cards.append(
            '<div class="day-card">'
            f'<div class="day-head"><strong>{esc(weekday)}, {esc(date_text)}</strong><span class="count">{len(day_slots)}</span></div>'
            + "".join(slot_html) + "</div>"
        )
    calendar = f"""
    <section class="panel" id="calendar">
      <div class="panel-head">
        <div class="panel-title"><span class="ico">□</span><div><h2>Контент-календарь</h2><div class="panel-sub">Публикации и дедлайны текущего цикла</div></div></div>
        <a class="btn secondary small" href="#plan">К плану →</a>
      </div>
      <div class="calendar-scroll"><div class="calendar-grid" style="--days:{max(1, len(day_cards))}">{"".join(day_cards) or '<div class="empty">План пока не содержит публикаций.</div>'}</div></div>
    </section>
    """

    package_html = []
    for index, (key, group) in enumerate(sorted(
        groups.items(),
        key=lambda item: min((s.get("publish_at") or "" for s in item[1]), default="")
    )):
        task_ids = {slot.get("task_id") for slot in group if slot.get("task_id")}
        package_artifacts = []
        for task_id in task_ids:
            package_artifacts.extend([a for a in by_task.get(str(task_id), []) if a.kind in MATERIAL_KINDS])
        package_html.append(package_card(
            product=product,
            plan_row=plan_row,
            plan=plan,
            key=key,
            group=group,
            package_artifacts=package_artifacts,
            index=index,
        ))
    package_section = f"""
    <section class="panel" id="plan">
      <div class="panel-head">
        <div class="panel-title"><span class="ico">▱</span><div><h2>Контент-пакеты</h2><div class="panel-sub">Одна тема → адаптации для всех нужных площадок</div></div></div>
        <span class="status {status_class((plan or {}).get("approval_state"))}">{esc(status_label((plan or {}).get("approval_state")))}</span>
      </div>
      <div class="package-tools">
        <div class="tabs"><button class="tab active" type="button">Все · {len(groups)}</button><button class="tab" type="button">Готово · {ready}</button><button class="tab" type="button">Ошибки · {failed}</button></div>
        {action_html}
      </div>
      <div class="package-grid">{"".join(package_html) or '<div class="empty">Контент-пакеты ещё не сформированы.</div>'}</div>
      <div class="unknowns">
        <details><summary>Что ещё нужно уточнить · {len((plan or {}).get("unknowns") or [])}</summary>
        <ul>{"".join(f"<li>{esc(x)}</li>" for x in ((plan or {}).get("unknowns") or [])) or "<li>Критичных неизвестных нет.</li>"}</ul></details>
      </div>
    </section>
    """

    library_cards = "".join(material_card(record) for record in material_records[:60])
    library = f"""
    <section class="panel" id="materials">
      <div class="panel-head">
        <div class="panel-title"><span class="ico">▤</span><div><h2>Подготовленные материалы</h2><div class="panel-sub">Статьи, посты и сценарии из всех контент-пакетов</div></div></div>
        <span class="chip blue">Всего · {len(material_records)}</span>
      </div>
      <div class="material-toolbar">
        <div class="tabs">
          <button class="tab active" type="button" data-filter="all">Все</button>
          <button class="tab" type="button" data-filter="article">Статьи</button>
          <button class="tab" type="button" data-filter="post">Посты</button>
          <button class="tab" type="button" data-filter="video">Сценарии</button>
        </div>
        <label class="search"><input id="material-search" type="search" placeholder="Поиск по материалам…"></label>
      </div>
      <div class="material-grid">{library_cards or '<div class="empty">Готовых материалов пока нет.</div>'}</div>
    </section>
    """

    first_material = material_records[0] if material_records else None
    dialogs = "".join(asset_dialog(record) for record in material_records[:60])
    plan_state = (plan or {}).get("approval_state") or "draft"
    generated = (plan or {}).get("generated_at")
    review = (plan or {}).get("review_at")

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#f4f8fd">
  <title>Growth Studio · {esc(product.name)}</title>
  <link rel="stylesheet" href="/studio/assets/studio.css?v=3">
</head>
<body>
<div class="studio-page"><div class="studio-layout">
  {sidebar_html()}
  <main class="main">
    <header class="topbar">
      <div>
        <div class="eyebrow">GROWTH STUDIO <span class="beta">BETA</span></div>
        <h1>Growth Studio</h1>
        <p class="subtitle">Готовые идеи, тексты и медиаконтент для роста ваших бронирований.</p>
      </div>
      <div class="top-actions">
        <div class="cycle-card">
          <div class="cycle-head"><span>Текущий цикл</span><b>{esc(horizon)} дней · {esc(status_label(plan_state))}</b></div>
          <div class="progress"><span style="width:{progress}%"></span></div>
        </div>
        <a class="product-pill" href="/studio?choose=1">
          <span class="product-avatar">S</span><div><b>{esc(product.name)}</b><span>{esc(product.slug)}</span></div>
        </a>
      </div>
    </header>
    {mobile_dashboard_html(
        product=product,
        plan_row=plan_row,
        plan=plan,
        slots=slots,
        groups=groups,
        by_task=by_task,
        material_records=material_records,
        action_html=action_html,
        horizon=horizon,
        channels=channels,
    )}
    <div class="desktop-only-dashboard">
    {kpis}
    <div class="workspace">
      <div class="stack">
        {calendar}
        {package_section}
        {library}
        <section class="panel" id="analytics">
          <div class="panel-head"><div class="panel-title"><span class="ico">⌁</span><div><h2>Цикл и контроль</h2><div class="panel-sub">Измерения начнутся после ручной публикации и регистрации результатов</div></div></div></div>
          <div class="package-tools">
            <span class="chip">Сформирован: {fmt_dt(generated)}</span>
            <span class="chip">Пересмотр: {fmt_dt(review)}</span>
            <span class="chip">Публикация остаётся ручной</span>
          </div>
        </section>
      </div>
      {selected_preview(first_material)}
    </div>
    </div>
  </main>
  {mobile_nav_html()}
</div></div>
{dialogs}
<script src="/studio/assets/studio.js?v=3"></script>
</body></html>"""


def product_selector(products: list[Product]) -> str:
    cards = "".join(
        f'<a class="product-link" href="/studio/products/{p.id}"><strong>{esc(p.name)}</strong><small>{esc(p.slug)} · открыть Growth Studio →</small></a>'
        for p in products
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Growth Studio</title><link rel="stylesheet" href="/studio/assets/studio.css?v=2"></head>
<body><main class="product-home">
<section class="panel hero">{brand_html()}<div class="eyebrow" style="justify-content:center;margin-top:18px">GROWTH STUDIO <span class="beta">BETA</span></div>
<h1>Выберите проект</h1><p class="subtitle">Контент-план, готовые материалы и сценарии собраны в одном интерфейсе.</p></section>
<div class="product-list">{cards or '<div class="empty">Проекты ещё не добавлены.</div>'}</div>
</main></body></html>"""


@router.get("/studio/assets/studio.css")
def studio_css():
    response = Response(STUDIO_CSS, media_type="text/css")
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@router.get("/studio/assets/studio.js")
def studio_js():
    response = Response(STUDIO_JS, media_type="application/javascript")
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


@router.get("/studio")
def studio(choose: bool = False, db: Session = Depends(get_db)):
    products = list(db.scalars(select(Product).order_by(Product.name).limit(100)))
    preferred = next((p for p in products if p.slug == "servgo"), None)
    if preferred is not None and not choose:
        return RedirectResponse(f"/studio/products/{preferred.id}", status_code=302)
    response = HTMLResponse(product_selector(products))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}", response_class=HTMLResponse)
def studio_product(product_id: UUID, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    response = HTMLResponse(product_dashboard(db, product))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/studio/products/{product_id}/plans/{plan_id}/approve")
def studio_approve(product_id: UUID, plan_id: UUID, db: Session = Depends(get_db)):
    row = db.get(Artifact, plan_id)
    if row is None or row.product_id != product_id:
        raise HTTPException(404, "Plan not found")
    try:
        approve_plan(db, get_settings(), plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}", status_code=303)


@router.post("/studio/products/{product_id}/plans/{plan_id}/pause")
def studio_pause(product_id: UUID, plan_id: UUID, db: Session = Depends(get_db)):
    row = db.get(Artifact, plan_id)
    if row is None or row.product_id != product_id:
        raise HTTPException(404, "Plan not found")
    try:
        pause_plan(db, plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}", status_code=303)


@router.post("/studio/products/{product_id}/plans/{plan_id}/packages/{package_key}/queue")
def studio_queue_package(product_id: UUID, plan_id: UUID, package_key: str, db: Session = Depends(get_db)):
    row = db.get(Artifact, plan_id)
    if row is None or row.product_id != product_id or row.kind != "editorial_plan":
        raise HTTPException(404, "Plan not found")
    plan = parse_json(row)
    if plan.get("approval_state") != "active":
        raise HTTPException(409, "Approve the editorial plan before queueing its packages")
    slots = [slot for slot in (plan.get("slots") or []) if slot.get("package_key") == package_key]
    if not slots:
        raise HTTPException(404, "Package not found")
    topic = slots[0].get("topic")
    audience = slots[0].get("audience")
    objective = slots[0].get("objective") or ""
    channels = ", ".join(sorted({slot.get("platform", "") for slot in slots}))
    queue_content(
        db,
        product_id,
        topic,
        dedupe_key=f"calendar:{plan_id}:{package_key}",
        audience=audience,
        editorial_brief=(
            f"Цель пакета: {objective}\n"
            f"Площадки: {channels}\n"
            "План подтверждает только подготовку; публикация вручную."
        ),
    )
    return RedirectResponse(f"/studio/products/{product_id}", status_code=303)
