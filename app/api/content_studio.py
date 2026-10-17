"""Owner-facing visual content workspace for AI Growth OS.

The public production entrypoint is protected by the web edge. This module keeps
publication manual and preserves existing editorial approval guards.
"""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo
from uuid import UUID
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse, FileResponse
from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from app.api.content_studio_design import STUDIO_CSS, STUDIO_JS
from app.api.content_studio_brand import SERVGO_FAVICON_SVG, servgo_brand_html
from app.api.content_studio_article import render_article
from app.content.render import asset_path
from app.api.content_studio_activity import (work_items, notice_items, notice_html,
    activity_html, activity_overview, task_detail_html, RESULT_KINDS)
from app.api.content_studio_ui import create_content_html, review_html, channels_html
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.content.calendar import (PLATFORMS, approve_plan, pause_plan,
    reschedule_slot, queue_plan, save_preferences)
from app.content.service import queue_content
from app.content.revisions import post_history, queue_post_edit
from app.content.revisions import source_fingerprint
from app.content.manual_edits import editable_fields, save_manual_edit
from app.content.creative import CreativeRequest, queue_creative
from app.content.telegram_publishing import (verify_channel, save_verified_channel,
    connected_channels, schedule_post, cancel_scheduled_post, validate_telegram_text,
    reconcile_publication)
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])

MATERIAL_KINDS = {
    "article", "channel_text", "video_script", "content_distribution",
    "dzen_brief", "telegram_brief", "short_video_brief",
    "story_brief", "static_image_brief", "creative_brief", "video_render", "whiteboard_video",
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
    "scheduled": "Назначено к отправке",
    "published": "Опубликовано",
    "delivery_unknown": "Отправку нужно проверить",
    "completed": "Готово",
    "failed": "Ошибка",
    "missed": "Пропущено",
    "cancelled": "Отменено",
    "awaiting_approval": "На проверке",
    "approved": "Утверждено",
    "rejected": "Предыдущая версия",
    "failed_before_send": "Не отправлено",
    "sending": "Отправляется",
}


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def parse_json(record: Artifact) -> dict:
    try:
        value = json.loads(record.content)
        return value if isinstance(value, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def latest_plan(db: Session, product_id: UUID, *, prefer_active: bool = True):
    """Active production plan must not disappear when a newer draft is created.

    Set prefer_active=False when the owner explicitly asks to review the latest draft.
    """
    rows = list(db.scalars(
        select(Artifact)
        .where(Artifact.product_id == product_id, Artifact.kind == "editorial_plan")
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(100)
    ))
    parsed = [(row, parse_json(row)) for row in rows]
    parsed = [(row, value) for row, value in parsed if value]
    parsed.sort(key=lambda item: (item[1].get("generated_at") or "", str(item[0].id)), reverse=True)
    if prefer_active:
        active = next((item for item in parsed if item[1].get("approval_state") == "active"), None)
        if active:
            return active
    return parsed[0] if parsed else (None, None)


def article_preview_html(text: str) -> str:
    """Readable, escaped Markdown. Raw text remains available for editing."""
    return render_article(text)



def latest_draft(db: Session, product_id: UUID):
    """Find an awaiting-review draft without hiding the active plan."""
    rows = list(db.scalars(
        select(Artifact)
        .where(Artifact.product_id == product_id, Artifact.kind == "editorial_plan")
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(100)
    ))
    drafts = [(row, parse_json(row)) for row in rows]
    drafts = [item for item in drafts if item[1].get("approval_state") == "draft"]
    drafts.sort(key=lambda item: (item[1].get("generated_at") or "", str(item[0].id)), reverse=True)
    return drafts[0] if drafts else (None, None)


def reconcile_plan_states(db: Session, product_id: UUID, plan: dict | None):
    """Display actual job state even before the periodic calendar tick runs.

    Read-only reconciliation: never claim a package was published, and never
    mutate stored plan JSON while preparing an HTML response.
    """
    if not plan:
        return plan
    slots = plan.get("slots") or []
    raw_ids = {slot.get("task_id") for slot in slots if slot.get("task_id")}
    task_ids = set()
    for raw in raw_ids:
        try:
            task_ids.add(UUID(str(raw)))
        except (ValueError, TypeError, AttributeError):
            continue
    if not task_ids:
        return plan
    tasks = {
        str(task.id): task
        for task in db.scalars(
            select(Task).where(Task.product_id == product_id, Task.id.in_(task_ids))
        )
    }
    mapping = {
        TaskStatus.QUEUED: "queued",
        TaskStatus.RUNNING: "preparing",
        TaskStatus.WAITING_APPROVAL: "ready_for_review",
        TaskStatus.COMPLETED: "ready_for_review",
        TaskStatus.FAILED: "failed",
        TaskStatus.CANCELLED: "cancelled",
    }
    # A display-only copy protects persistence semantics (including 'missed').
    updated = dict(plan)
    updated["slots"] = []
    for slot in slots:
        current = dict(slot)
        task = tasks.get(str(slot.get("task_id")))
        if task is not None and current.get("state") not in {"missed", "scheduled", "published", "delivery_unknown"}:
            current["state"] = mapping.get(task.status, current.get("state", "planned"))
        updated["slots"].append(current)
    return updated


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
    if "cancelled" in states:
        return "cancelled"
    if states and states <= {"completed"}:
        return "completed"
    return "planned"


def artifact_kind(record: Artifact) -> str:
    if record.kind == "article":
        return "article"
    if record.kind in {"channel_text", "dzen_brief", "telegram_brief"}:
        return "post"
    if record.kind in {"video_script", "short_video_brief", "video_render", "whiteboard_video"}:
        return "video"
    if record.kind in {"creative_brief", "story_brief", "static_image_brief"}:
        return "creative"
    return "other"


def artifact_kind_label(record: Artifact, value: dict | None = None) -> str:
    value = value or parse_json(record)
    if record.kind == "article":
        return "Статья"
    if record.kind == "video_render":
        return "Готовый видеоролик MP4"
    if record.kind == "whiteboard_video":
        return "Рисованное видео с озвучкой"
    if record.kind == "channel_text":
        return platform_label(value.get("channel", "")) or "Пост"
    if record.kind in {"video_script", "short_video_brief"}:
        platform = (value.get("production_brief") or value).get("target_platform")
        return f"Сценарий · {platform_label(platform)}" if platform else "Сценарий"
    if record.kind == "creative_brief":
        format_name = {"short_video":"Сценарий ролика", "story":"Stories", "static_image":"Визуальный бриф"}.get(value.get("format"), "Креативный бриф")
        return format_name + " · " + platform_label(value.get("platform"))
    if record.kind == "dzen_brief":
        return "Дзен"
    if record.kind == "telegram_brief":
        return "Telegram"
    if record.kind == "story_brief":
        return "Stories"
    if record.kind == "static_image_brief":
        return "Визуал"
    return record.kind.replace("_", " ").title()


def display_material_title(record: Artifact, value: dict | None = None) -> str:
    """Show business-facing labels without modifying the original artifact title."""
    value = value or parse_json(record)
    raw = (record.title or "").strip()
    normalized = raw.casefold().replace("_", " ").replace("-", " ")
    aliases = {
        "telegram derivative brief": "Бриф для Telegram",
        "dzen derivative brief": "Бриф для Дзена",
        "story derivative brief": "Сценарий Stories",
        "static image derivative brief": "Бриф изображения",
        "short video derivative brief": "Идея короткого видео",
        "content distribution": "План публикаций",
    }
    if normalized in aliases:
        return aliases[normalized]
    if normalized.endswith(" derivative brief"):
        channel = normalized.removesuffix(" derivative brief")
        return f"Бриф для {platform_label(channel)}"
    return raw or artifact_kind_label(record, value)


def artifact_text(record: Artifact, value: dict | None = None) -> str:
    value = value or parse_json(record)
    if not value:
        return record.content
    if record.kind == "video_render":
        return "Готовый видеофайл MP4 — откройте для просмотра или скачивания."
    if record.kind == "whiteboard_video":
        return "Рисованное видео MP4 — нажмите, чтобы посмотреть и скачать."
    if record.kind == "article":
        return str(value.get("full_article") or value.get("reader_article") or value.get("body") or record.content)
    if record.kind == "channel_text":
        return str(value.get("body") or value.get("text") or record.content)
    if record.kind == "creative_brief":
        return str(value.get("preview_text") or record.content)
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
    return servgo_brand_html() + '<a class="back-admin" href="https://servgo.ru/admin">← Вернуться в админку ServGo</a>'


def sidebar_html(product_id: UUID, active: str = "overview") -> str:
    """Four everyday destinations; advanced features deliberately secondary."""
    base = f"/studio/products/{product_id}"
    def item(name: str, href: str, icon: str, label: str) -> str:
        current = ' class="active" aria-current="page"' if active == name else ""
        return f'<a{current} href="{href}"><span class="nav-ico" aria-hidden="true">{icon}</span>{label}</a>'
    return f"""
    <aside class="sidebar">
      {servgo_brand_html(href=base)}
      <a class="back-admin" href="https://servgo.ru/admin">← Вернуться в админку ServGo</a>
      <div class="nav-label">МОЯ РАБОТА</div>
      <nav class="nav" aria-label="Основная навигация">
        {item("overview", base, "⌂", "Главная")}
        {item("create", base + "/create", "＋", "Создать материал")}
        {item("work", base + "/work", "◴", "В работе")}
        {item("materials", base + "/materials", "▤", "Мои материалы")}
        <a href="https://servgo.ru/mail"><span class="nav-ico" aria-hidden="true">✉</span>Почта</a>
      </nav>
      <details class="sg-side-extras">
        <summary>Настройки маркетинга и другое</summary>
        <nav class="nav" aria-label="Дополнительные инструменты">
          {item("review", base + "/review", "✓", "На согласовании")}
          {item("plan", base + "/plan", "☷", "Контент-план")}
          {item("calendar", base + "/calendar", "▦", "Календарь")}
          {item("channels", base + "/channels", "↗", "Публикации")}
          {item("analytics", base + "/analytics", "⌁", "Результаты")}
          {item("comments", base + "/comments", "☷", "Комментарии")}
          {item("intelligence", f"/intelligence?product_id={product_id}", "◎", "Бизнес-разведка")}
          {item("profile", base + "/profile", "▣", "Профиль компании")}
          {item("team", "/studio/team", "♧", "ИИ-команда")}
          {item("tune", "/studio/tune", "⚙", "Настройка агентов")}
          <a href="/studio?choose=1"><span class="nav-ico" aria-hidden="true">⇄</span>Другой проект</a>
        </nav>
      </details>
      <div class="sidebar-spacer"></div>
      <div class="sidebar-foot">ServGo · Маркетинг</div>
    </aside>
    """


def mobile_nav_html(product_id: UUID, active: str = "overview") -> str:
    base = f"/studio/products/{product_id}"
    def item(name: str, href: str, icon: str, label: str) -> str:
        current = ' class="active" aria-current="page"' if active == name else ""
        return f'<a{current} href="{href}"><span class="m-ico" aria-hidden="true">{icon}</span>{label}</a>'
    return f"""
    <nav class="mobile-nav" aria-label="Быстрая навигация">
      {item("overview", base, "⌂", "Главная")}
      {item("create", base + "/create", "＋", "Создать")}
      {item("work", base + "/work", "◴", "В работе")}
      {item("materials", base + "/materials", "▤", "Готово")}
    </nav>
    """



def mobile_header_html(product: Product) -> str:
    """One brand bar on mobile, not a second bottom navigation overlay."""
    base = f"/studio/products/{product.id}"
    return f"""
    <header class="sg-mobile-shell-head">
      {servgo_brand_html(compact=True, href=base)}
      <a class="sg-mobile-project" href="/studio?choose=1"
         aria-label="Сменить компанию: {esc(product.name)}">{esc(product.name)} <span aria-hidden="true">⌄</span></a>
    </header>
    """


def asset_dialog(record: Artifact) -> str:
    value = parse_json(record)
    title = esc(display_material_title(record, value))
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


def material_card(product_id: UUID, record: Artifact) -> str:
    value = parse_json(record)
    kind = artifact_kind(record)
    title = esc(display_material_title(record, value))
    label = esc(artifact_kind_label(record, value))
    text = artifact_text(record, value)
    meta = excerpt(text, 90)
    created = record.created_at.strftime("%d.%m") if record.created_at else ""
    return (
        f'<a class="material-card" data-material data-kind="{kind}" '
        f'data-search="{esc(record.title + " " + label + " " + meta)}" '
        f'href="/studio/products/{product_id}/materials/{record.id}">'
        f'<div class="material-cover {esc(record.kind)}"><span class="material-kind">{label}</span></div>'
        '<div class="material-body">'
        f'<h4>{title}</h4><p>{esc(meta)}</p>'
        f'<div class="package-foot"><span>{esc(created)}</span><span>Открыть →</span></div>'
        '</div></a>'
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
    if plan_row and plan and plan.get("approval_state") == "active" and (not task_ids or state in {"failed", "cancelled"}):
        queue_button = (
            f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/packages/{esc(key)}/queue">'
            f'<button class="btn secondary small" type="submit">{"Повторить подготовку" if task_ids else "Подготовить сейчас"}</button></form>'
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
        <div class="preview-actions"><a class="btn primary small" href="/studio/products/{product.id}/packages/{quote(str(key), safe='')}">Открыть пакет</a>{open_button}{queue_button}</div>
      </div>
    </article>
    """



def mobile_logo_html(product_id: UUID) -> str:
    base = f"/studio/products/{product_id}"
    return f"""
    <div class="m-brand sg-mobile-brand">
      {servgo_brand_html(compact=True, href=base)}
      <div class="m-logo-actions">
        <a class="m-back-admin" href="https://servgo.ru/admin" aria-label="Вернуться в админку ServGo">← Админка</a>
        <a class="m-bell" href="{base}/review" aria-label="Материалы на проверке">✓</a>
        <a class="m-avatar" href="/studio?choose=1" aria-label="Сменить компанию">⇄</a>
      </div>
    </div>
    """


def mobile_calendar_html(product_id: UUID, slots: list[dict]) -> str:
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
            f'<a class="m-day{" active" if index == 0 else ""}" href="/studio/products/{product_id}/calendar?date={esc(_)}">'
            f'<b>{esc(weekday)}</b><span>{esc(date_text)}</span><i></i></a>'
        )
    timeline = []
    for slot in ordered[:5]:
        dt = parse_dt(slot.get("publish_at"))
        time_text = dt.strftime("%H:%M") if dt else "—"
        timeline.append(
            f'<a class="m-event" href="/studio/products/{product_id}/packages/{quote(str(slot.get("package_key") or "unknown"), safe="")}">'
            f'<time>{esc(time_text)}</time>'
            f'<span class="m-platform">{esc(platform_short(slot.get("platform") or ""))}</span>'
            '<div class="m-event-copy">'
            f'<b>{esc(slot.get("topic"))}</b>'
            f'<small>{esc(platform_label(slot.get("platform") or ""))}</small>'
            '</div><span class="m-more">›</span></a>'
        )
    timeline_html = "".join(timeline) or '<div class="empty">Публикаций пока нет.</div>'
    return (
        '<section class="m-card m-calendar" id="m-calendar">'
        f'<div class="m-section-head"><div><span class="m-section-icon">▱</span><b>Контент-календарь</b></div><a href="/studio/products/{product_id}/calendar" aria-label="Открыть календарь">›</a></div>'
        f'<div class="m-days">{"".join(days)}</div>'
        f'<div class="m-timeline">{timeline_html}</div>'
        f'<a class="m-calendar-open" href="/studio/products/{product_id}/calendar"><span>□</span> Открыть календарь <b>→</b></a>'
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
    package_url = f"/studio/products/{product.id}/packages/{quote(str(key), safe='')}"
    return (
        f'<a class="m-package m-scene-{index % 4}" href="{package_url}">'
        f'<div class="m-package-cover"><span class="m-package-status {status_class(state)}">{esc(status_label(state))}</span></div>'
        '<div class="m-package-body">'
        f'<h3>{esc(topic)}</h3>'
        f'<p>{esc(group[0].get("objective") or "Материалы пакета для нескольких площадок")}</p>'
        f'<div class="m-channel-row">{chips}<em>{len(package_artifacts)} материала</em></div>'
        f'<div class="m-package-date">□ {fmt_dt(first_publish)}</div>'
        '</div></a>'
    )


def mobile_material_card(product_id: UUID, record: Artifact) -> str:
    value = parse_json(record)
    kind = artifact_kind(record)
    return (
        f'<a class="m-material {kind}" href="/studio/products/{product_id}/materials/{record.id}">'
        f'<span class="m-material-type">{esc(artifact_kind_label(record, value))}</span>'
        f'<span class="m-material-thumb">{("▧" if kind == "article" else "▶" if kind == "video" else "✦")}</span>'
        f'<b>{esc(display_material_title(record, value))}</b>'
        f'<small>{esc(excerpt(artifact_text(record, value), 55))}</small>'
        '</a>'
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
    materials = "".join(mobile_material_card(product.id, record) for record in material_records[:6])
    return f"""
    <div class="mobile-only-dashboard">
      {mobile_logo_html(product.id)}
      <header class="m-hero">
        <div class="m-title-row"><h1>Growth Studio</h1><span class="beta">BETA</span></div>
        <p>Готовые идеи, тексты и медиаконтент для роста ваших бронирований</p>
      </header>

      <section class="m-kpis">
        <div class="m-kpi blue"><span class="m-kpi-icon">▱</span><strong>{len(groups)}<small>пакетов</small></strong><p>запланировано<br>на период</p></div>
        <div class="m-kpi green"><span class="m-kpi-icon">□</span><strong>{esc(horizon)}<small>дней</small></strong><p>активный<br>контент-план</p></div>
        <div class="m-kpi purple"><span class="m-kpi-icon">⌘</span><strong>{len(channels)}<small>площадок</small></strong><div class="m-mini-platforms">{"".join(f"<span>{esc(platform_short(x))}</span>" for x in channels[:4])}</div></div>
      </section>

      {mobile_calendar_html(product.id, slots)}

      <section class="m-card m-packages" id="m-packages">
        <div class="m-section-head"><div><span class="m-section-icon">▱</span><b>Контент-пакеты</b></div><a class="m-all-pill" href="/studio/products/{product.id}/plan">Все</a></div>
        <div class="m-package-actions">{action_html}</div>
        <div class="m-package-carousel">{"".join(package_cards) or '<div class="empty">Контент-пакеты ещё не сформированы.</div>'}</div>
        <div class="m-dots"><i class="active"></i><i></i><i></i></div>
      </section>

      <section class="m-card m-materials" id="m-materials">
        <div class="m-section-head"><div><span class="m-section-icon">▤</span><b>Подготовленные материалы</b></div><a href="/studio/products/{product.id}/materials">Все</a></div>
        <div class="m-material-strip">{materials or '<div class="empty">Готовых материалов пока нет.</div>'}</div>
      </section>
    </div>
    """;


def selected_preview(record: Artifact | None) -> str:
    if record is None:
        return (
            '<section class="panel preview"><div class="preview-empty">'
            '<img class="sg-preview-logo" src="/studio/assets/servgo.svg" alt="" width="44" height="44" style="margin:0 auto 14px">'
            '<b>Материалы появятся здесь</b><p>После подготовки пакета можно будет быстро открыть статью, пост или раскадровку.</p>'
            '</div></section>'
        )
    value = parse_json(record)
    text = artifact_text(record, value)
    return f"""
    <section class="panel preview">
      <div class="preview-hero"><img class="sg-preview-logo" src="/studio/assets/servgo.svg" alt="" width="44" height="44"></div>
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
    plan = reconcile_plan_states(db, product.id, plan)
    tasks = list(db.scalars(
        select(Task)
        .where(Task.product_id == product.id, Task.assigned_agent.in_(["content_factory", "editorial_planner", "content_creative", "content_editor"]))
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

    content_tasks = [task for task in tasks if task.assigned_agent in {"content_factory", "content_creative", "content_editor"}]
    # Manual/standalone assets belong in the owner's library too; they need
    # not have originated from a content factory task.
    material_records = [
        artifact for artifact in artifacts
        if artifact.kind in MATERIAL_KINDS
    ]
    # Deduplicate artifacts by id while retaining newest-first order.
    seen = set()
    material_records = [a for a in material_records if not (a.id in seen or seen.add(a.id))]

    ready = sum(1 for slot in slots if slot.get("state") in {"ready_for_review", "completed"})
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
      <div class="kpi orange"><span class="kpi-icon">✓</span><div><strong>{ready}</strong><small>слотов<br>готово к проверке</small></div></div>
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
        <a class="btn secondary small" href="/studio/products/{product.id}/plan">К плану →</a>
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
        <div class="tabs"><a class="tab active" href="/studio/products/{product.id}/plan">Все · {len(groups)}</a><a class="tab" href="/studio/products/{product.id}/plan?status=ready">Готово · {ready}</a><a class="tab" href="/studio/products/{product.id}/plan?status=failed">Ошибки · {failed}</a></div>
        {action_html}
      </div>
      <div class="package-grid">{"".join(package_html) or '<div class="empty">Контент-пакеты ещё не сформированы.</div>'}</div>
      <div class="unknowns">
        <details><summary>Что ещё нужно уточнить · {len((plan or {}).get("unknowns") or [])}</summary>
        <ul>{"".join(f"<li>{esc(x)}</li>" for x in ((plan or {}).get("unknowns") or [])) or "<li>Критичных неизвестных нет.</li>"}</ul></details>
      </div>
    </section>
    """

    library_cards = "".join(material_card(product.id, record) for record in material_records[:60])
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
    draft_row, draft = latest_draft(db, product.id)
    # One prominent, self-contained action card instead of raw text appended
    # to a generic .panel (which collided with its layout on narrow screens).
    new_draft_notice = (
        f'<section class="sg-draft-alert" role="region" aria-labelledby="sg-draft-title">'
        '<div class="sg-draft-alert-icon" aria-hidden="true">✓</div>'
        '<div class="sg-draft-alert-copy">'
        '<span class="sg-draft-alert-eyebrow">ГОТОВО К ПРОВЕРКЕ</span>'
        '<h2 id="sg-draft-title">Новый контент-план готов</h2>'
        '<p>ИИ подготовил новый вариант. Текущий план продолжит работать, '
        'пока вы не утвердите новый.</p>'
        '</div>'
        f'<a class="btn primary sg-draft-alert-action" href="/studio/products/{product.id}/plan?view=draft">'
        'Посмотреть план <span aria-hidden="true">→</span></a>'
        '</section>'
        if draft_row and draft_row != plan_row and plan_state == "active" else ""
    )
    generated = (plan or {}).get("generated_at")
    review = (plan or {}).get("review_at")

    review_count = db.scalar(select(func.count(Artifact.id)).where(
        Artifact.product_id == product.id,
        Artifact.kind.in_(["channel_text", "creative_brief", "article", "video_script", "video_render"]),
        Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
    )) or 0
    publishing_counts = dict(db.execute(
        select(Publication.status, func.count(Publication.id))
        .where(Publication.product_id == product.id, Publication.channel == "telegram")
        .group_by(Publication.status)
    ).all())
    scheduled_count = publishing_counts.get("scheduled", 0)
    unsafe_count = sum(publishing_counts.get(status, 0)
                       for status in ("delivery_unknown", "sending", "failed_before_send"))
    attention_html = f"""
    <section class="sg-workflow-banner" aria-labelledby="start-working">
      <div>
        <span class="sg-overline">SERVGO · ИИ-МАРКЕТИНГ</span>
        <h2 id="start-working">Что будем создавать?</h2>
        <p>Укажите тему — ИИ-команда подготовит материал.
        Все этапы и результаты можно посмотреть в разделе «В работе».</p>
      </div>
      <div class="sg-hero-actions">
        <a class="btn primary" href="/studio/products/{product.id}/create">＋ Создать материал</a>
        <a class="btn secondary" href="/studio/products/{product.id}/work">Мои задания →</a>
      </div>
    </section>
    """
    overview_extra = f"""
    <div class="sg-priority-strip">
      <a class="sg-priority-item" href="/studio/products/{product.id}/review">
        <strong>{review_count}</strong><span>Ждут проверки</span><em>Открыть →</em></a>
      <a class="sg-priority-item" href="/studio/products/{product.id}/channels">
        <strong>{scheduled_count}</strong><span>В расписании Telegram</span><em>Публикации →</em></a>
      <a class="sg-priority-item" href="/studio/products/{product.id}/channels">
        <strong>{unsafe_count}</strong><span>Отправку нужно проверить</span><em>Статусы →</em></a>
    </div>
    <nav class="sg-quick-links" aria-label="Дополнительные инструменты">
      <a href="/studio/products/{product.id}/plan">Контент-план</a>
      <a href="/studio/products/{product.id}/calendar">Календарь</a>
      <a href="/studio/products/{product.id}/review">На согласовании</a>
      <a href="/studio/products/{product.id}/whiteboard">Рисованное видео</a>
      <a href="/studio/products/{product.id}/comments">Комментарии</a>
      <a href="/intelligence?product_id={product.id}">Бизнес-разведка</a>
      <a href="/studio/products/{product.id}/channels">Публикации</a>
      <a href="/studio/products/{product.id}/analytics" aria-label="Аналитика">Аналитика</a>
      <a href="/studio/team">ИИ-команда</a>
      <a href="/studio/tune">Настройка агентов</a>
      <a href="https://servgo.ru/mail">Почта</a>
      <a href="/studio?choose=1" aria-label="Выбрать проект">Другой проект</a>
    </nav>
    """

    upcoming_cards = []
    future_slots = [slot for slot in slots if parse_dt(slot.get("publish_at")) and parse_dt(slot.get("publish_at")) >= datetime.now(timezone.utc)]
    for slot in sorted(future_slots, key=lambda x: x.get("publish_at") or "")[:4]:
        package_key = quote(str(slot.get("package_key") or "unknown"), safe="")
        upcoming_cards.append(
            f'<a class="sg-mini-row" href="/studio/products/{product.id}/packages/{package_key}">'
            f'<span class="sg-mini-date">{fmt_dt(slot.get("publish_at"))}</span>'
            f'<div><b>{esc(slot.get("topic") or "Материал")}</b>'
            f'<small>{esc(platform_label(slot.get("platform")))} · '
            f'{esc(status_label(slot.get("state")))}</small></div>'
            '<span aria-hidden="true">↗</span></a>'
        )
    recent_cards = []
    for material in material_records[:4]:
        meta = parse_json(material)
        recent_cards.append(
            f'<a class="sg-mini-row" href="/studio/products/{product.id}/materials/{material.id}">'
            f'<span class="sg-mini-kind">▤</span><div><b>{esc(display_material_title(material, meta)[:75])}</b>'
            f'<small>{esc(artifact_kind_label(material, meta))} · '
            f'{esc(status_label(getattr(material.status, "value", material.status)))}</small></div>'
            '<span aria-hidden="true">↗</span></a>'
        )
    quick_feed = f"""
    <div class="sg-dashboard-feed">
      <section class="sg-surface"><div class="sg-section-head"><div><h2>Ближайшее по плану</h2>
        <p>Когда и какой материал готовим к публикации.</p></div>
        <a class="sg-mini-link" href="/studio/products/{product.id}/calendar">Календарь →</a></div>
        <div class="sg-mini-list">{"".join(upcoming_cards) or '<div class="sg-muted">Слотов пока нет. Начните с контент-плана.</div>'}</div>
      </section>
      <section class="sg-surface"><div class="sg-section-head"><div><h2>Последние материалы</h2>
        <p>Новые статьи, посты и сценарии вашей команды.</p></div>
        <a class="sg-mini-link" href="/studio/products/{product.id}/materials">Библиотека →</a></div>
        <div class="sg-mini-list">{"".join(recent_cards) or '<div class="sg-muted">Начните с создания первого материала.</div>'}</div>
      </section>
    </div>
    """

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#f7f9fc">
  <meta name="servgo-studio-build" content="20261013-unified10">
  <title>Growth Studio · {esc(product.name)}</title>
  <link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
  <link rel="stylesheet" href="/studio/assets/studio.css?v=10">
</head>
<body>
<div class="studio-page" data-studio-build="20261013-unified10"><div class="studio-layout">
  {sidebar_html(product.id)}
  {mobile_header_html(product)}
  {mobile_nav_html(product.id)}
  <main class="main">
    <div class="sg-global-status">{notice_html(product.id)}</div>
    <header class="topbar">
      <div>
        <div class="eyebrow">GROWTH STUDIO <span class="beta">BETA</span></div>
        <h1>Маркетинг компании</h1>
        <p class="subtitle">Создание, согласование и публикации — в одном понятном рабочем пространстве.</p>
      </div>
      <div class="top-actions">
        <div class="cycle-card">
          <div class="cycle-head"><span>Подготовлено {done_like} из {total_slots} слотов</span><b>{esc(horizon)} дней · {esc(status_label(plan_state))}</b></div>
          <div class="progress"><span style="width:{progress}%"></span></div>
        </div>
        <a class="product-pill" href="/studio?choose=1">
          <img class="product-avatar sg-product-avatar" src="/studio/assets/servgo.svg" alt="" width="38" height="38"><div><b>{esc(product.name)}</b><span>{esc(product.slug)}</span></div>
        </a>
      </div>
    </header>
    {attention_html}
    {new_draft_notice}
    {activity_overview(product.id, work_items(db, product.id, limit=16))}
    <details class="sg-legacy-details sg-dashboard-extra"><summary>Контент-план, календарь и другие инструменты</summary>
    {overview_extra}
    {quick_feed}
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
    <details class="sg-legacy-details"><summary>Расширенная информация</summary>
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
            <span class="chip">Telegram — по отдельному утверждению и при подключении канала</span>
          </div>
        </section>
      </div>
      {selected_preview(first_material)}
    </div>
    </div>
    </details>
    </details>
  </main>
</div></div>
{dialogs}
<script src="/studio/assets/studio.js?v=11"></script>
</body></html>"""



def studio_state(db: Session, product: Product, *, view: str = "current") -> dict:
    plan_row, plan = latest_plan(db, product.id)
    if view == "draft":
        draft_row, draft = latest_draft(db, product.id)
        if draft_row:
            plan_row, plan = draft_row, draft
    plan = reconcile_plan_states(db, product.id, plan)
    tasks = list(db.scalars(
        select(Task)
        .where(Task.product_id == product.id, Task.assigned_agent.in_(["content_factory", "editorial_planner", "content_creative", "content_editor"]))
        .order_by(Task.created_at.desc())
        .limit(250)
    ))
    artifacts = list(db.scalars(
        select(Artifact)
        .where(Artifact.product_id == product.id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(1000)
    ))
    by_task: dict[str, list[Artifact]] = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_id:
            by_task[str(artifact.task_id)].append(artifact)
    slots = (plan or {}).get("slots") or []
    groups: dict[str, list[dict]] = defaultdict(list)
    for slot in slots:
        groups[str(slot.get("package_key") or "unknown")].append(slot)
    seen = set()
    materials = [
        artifact for artifact in artifacts
        if artifact.kind in MATERIAL_KINDS and not (artifact.id in seen or seen.add(artifact.id))
    ]
    channels = sorted({str(slot.get("platform")) for slot in slots if slot.get("platform")})
    return {
        "plan_row": plan_row,
        "plan": plan or {},
        "tasks": tasks,
        "artifacts": artifacts,
        "by_task": by_task,
        "slots": slots,
        "groups": groups,
        "materials": materials,
        "channels": channels,
    }


def studio_page_shell(
    product: Product,
    *,
    active: str,
    title: str,
    subtitle: str,
    body: str,
    dialogs: str = "",
    actions: str = "",
) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#f7f9fc">
  <meta name="servgo-studio-build" content="20261013-unified10">
  <title>{esc(title)} · Growth Studio</title>
  <link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
  <link rel="stylesheet" href="/studio/assets/studio.css?v=10">
</head>
<body>
<div class="studio-page" data-studio-build="20261013-unified10"><div class="studio-layout">
  {sidebar_html(product.id, active)}
  {mobile_header_html(product)}
  {mobile_nav_html(product.id, active)}
  <main class="main route-main">
    <div class="sg-global-status">{notice_html(product.id)}</div>
    <div class="route-mobile-brand">{mobile_logo_html(product.id)}</div>
    <header class="route-page-head">
      <div>
        <a class="back-link" href="/studio/products/{product.id}">← Обзор</a>
        <div class="eyebrow">GROWTH STUDIO <span class="beta">BETA</span></div>
        <h1>{esc(title)}</h1>
        <p class="subtitle">{esc(subtitle)}</p>
      </div>
      <div class="route-actions">{actions}</div>
    </header>
    <nav class="sg-route-utility" aria-label="Перейти к другим действиям">
      <a href="/studio/products/{product.id}/create">＋ Создать</a>
      <a href="/studio/products/{product.id}/work">◴ В работе</a>
      <a href="/studio/products/{product.id}/materials">▤ Материалы</a>
      <details class="sg-inline-more">
        <summary>Ещё инструменты</summary>
        <div><a href="/studio/products/{product.id}/plan">Контент-план</a>
        <a href="/studio/products/{product.id}/calendar">Календарь</a>
        <a href="/studio/products/{product.id}/whiteboard">Рисованное видео</a>
        <a href="/studio/products/{product.id}/comments">Комментарии</a>
        <a href="/intelligence?product_id={product.id}">Бизнес-разведка</a>
        <a href="/studio/products/{product.id}/channels">Публикации</a>
        <a href="/studio/products/{product.id}/analytics">Аналитика</a>
        <a href="/studio/team">ИИ-команда</a>
        <a href="/studio/tune">Настройка агентов</a>
        <a href="https://servgo.ru/mail">Почта</a>
        <a href="/studio?choose=1">Другой проект</a></div>
      </details>
    </nav>
    <div class="route-content">{body}</div>
  </main>
</div></div>
{dialogs}
<script src="/studio/assets/studio.js?v=11"></script>
</body></html>"""


def plan_page(db: Session, product: Product, status: str = "all", view: str = "current") -> str:
    state = studio_state(db, product, view=view)
    plan_row = state["plan_row"]
    plan = state["plan"]
    groups = state["groups"]
    by_task = state["by_task"]
    action_html = ""
    if plan_row:
        if plan.get("approval_state") == "draft":
            action_html = (
                f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/approve">'
                '<button class="btn primary" type="submit">✓ Подтвердить план</button></form>'
            )
        elif plan.get("approval_state") == "active":
            action_html = (
                f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/pause">'
                '<button class="btn ghost" type="submit">Ⅱ Поставить на паузу</button></form>'
            )

    cards = []
    for index, (key, group) in enumerate(sorted(
        groups.items(), key=lambda item: min((s.get("publish_at") or "" for s in item[1]), default="")
    )):
        group_state = state_for_group(group)
        if status == "ready" and group_state != "ready_for_review":
            continue
        if status == "failed" and group_state != "failed":
            continue
        task_ids = {slot.get("task_id") for slot in group if slot.get("task_id")}
        package_artifacts: list[Artifact] = []
        for task_id in task_ids:
            package_artifacts.extend([a for a in by_task.get(str(task_id), []) if a.kind in MATERIAL_KINDS])
        cards.append(package_card(
            product=product, plan_row=plan_row, plan=plan, key=key, group=group,
            package_artifacts=package_artifacts, index=index,
        ))

    unknowns = plan.get("unknowns") or []
    draft_row, _ = latest_draft(db, product.id)
    _, active_plan = latest_plan(db, product.id)
    switcher = ""
    if view == "draft" and active_plan and active_plan.get("approval_state") == "active":
        switcher = f'<a class="btn secondary small" href="/studio/products/{product.id}/plan">← Действующий план</a>'
    elif draft_row and draft_row != plan_row:
        switcher = f'<a class="btn secondary small" href="/studio/products/{product.id}/plan?view=draft">Новый черновик →</a>'
    view_q = "&view=draft" if view == "draft" else ""
    plan_q = "?view=draft" if view == "draft" else ""
    pending_planner = next((task for task in state["tasks"]
                            if task.assigned_agent == "editorial_planner"
                            and task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}), None)
    planner_notice = (
        '<p class="sg-notice" role="status">Редактор готовит новый вариант контент-плана. '
        'Обновите эту страницу позже. Действующий план продолжит работать.</p>'
        if pending_planner else ""
    )
    body = f"""
    {planner_notice}
    <section class="panel route-summary">
      <div class="panel-head">
        <div class="panel-title"><span class="ico">▣</span><div><h2>{"Черновик плана" if view == "draft" else "Действующий план"}</h2>
        <div class="panel-sub">{esc(plan.get("horizon_days") or 14)} дней · пересмотр {fmt_dt(plan.get("review_at"))}</div></div></div>
        <span class="status {status_class(plan.get("approval_state"))}">{esc(status_label(plan.get("approval_state")))}</span>
      </div>
      {switcher}
      <div class="package-tools">
        <a class="tab {'active' if status == 'all' else ''}" href="/studio/products/{product.id}/plan{plan_q}">Все · {len(groups)}</a>
        <a class="tab {'active' if status == 'ready' else ''}" href="/studio/products/{product.id}/plan?status=ready{view_q}">Готово</a>
        <a class="tab {'active' if status == 'failed' else ''}" href="/studio/products/{product.id}/plan?status=failed{view_q}">Ошибки</a>
      </div>
    </section>
    <section class="package-grid route-package-grid">{"".join(cards) or '<div class="panel empty">По выбранному фильтру пакетов нет.</div>'}</section>
    <section class="sg-surface">
      <div class="sg-section-head"><div><span class="sg-overline">ПЕРЕСМОТР СТРАТЕГИИ</span>
      <h2>Нужен новый план?</h2>
      <p>Напишите свободно, что важно компании. ИИ предложит новый черновик; действующий план не изменится до утверждения.</p></div></div>
      <form class="sg-inline-form" method="post" action="/studio/products/{product.id}/plan/generate">
        <label class="sg-field"><span>Пожелания к следующему плану <em>· необязательно</em></span>
          <textarea name="requirements" maxlength="6000" rows="3" placeholder="Например: 4 поста в Telegram в неделю, короткие ролики для тёплой аудитории, акцент на преимуществах"></textarea>
        </label><button class="btn secondary" type="submit">✦ Подготовить новый контент-план</button>
      </form>
    </section>
    <section class="panel" id="unknowns">
      <div class="panel-head"><div class="panel-title"><span class="ico">✦</span><div><h2>Идеи и неизвестные</h2>
      <div class="panel-sub">То, что редактор пока считает непроверенным</div></div></div><span class="chip blue">{len(unknowns)}</span></div>
      <div class="route-list">{"".join(f'<div class="route-list-row"><span>?</span><p>{esc(x)}</p></div>' for x in unknowns) or '<div class="empty">Неизвестных нет.</div>'}</div>
    </section>
    """
    return studio_page_shell(
        product, active="plan", title="Контент-план",
        subtitle="Темы, площадки, сроки и состояние каждого контент-пакета.",
        body=body, actions=action_html,
    )


def calendar_page(db: Session, product: Product, selected_date: str | None = None) -> str:
    state = studio_state(db, product)
    slots = sorted(state["slots"], key=lambda slot: slot.get("publish_at") or "")
    linked = {str(slot.get("telegram_publication_id")) for slot in slots}
    pubs = list(db.scalars(select(Publication).where(
        Publication.product_id == product.id, Publication.channel == "telegram",
    ).order_by(Publication.created_at.desc())))
    intents = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product.id, Artifact.kind == "telegram_publish_intent",
    )))
    by_publication = {str(parse_json(row).get("publication_id")): parse_json(row) for row in intents}
    for pub in pubs:
        if str(pub.id) in linked:
            continue
        meta = by_publication.get(str(pub.id), {})
        source = db.get(Artifact, pub.artifact_id)
        scheduled = parse_dt(meta.get("scheduled_at"))
        display_when = scheduled.astimezone(ZoneInfo("Europe/Moscow")).isoformat() if scheduled and scheduled.tzinfo else meta.get("scheduled_at")
        slots.append({"publish_at": display_when, "topic": source.title if source else "Telegram-пост",
                      "platform": "telegram", "state": pub.status, "artifact_id": str(pub.artifact_id),
                      "objective": "Отдельно назначенная публикация"})
    slots.sort(key=lambda slot: parse_dt(slot.get("publish_at")).timestamp() if parse_dt(slot.get("publish_at")) else 0)
    dates = []
    for slot in slots:
        dt = parse_dt(slot.get("publish_at"))
        day = dt.date().isoformat() if dt else ""
        if day and day not in dates:
            dates.append(day)
    if selected_date:
        filtered = [slot for slot in slots if (parse_dt(slot.get("publish_at")) and parse_dt(slot.get("publish_at")).date().isoformat() == selected_date)]
    else:
        filtered = slots

    date_tabs = "".join(
        f'<a class="tab {"active" if selected_date == day else ""}" href="/studio/products/{product.id}/calendar?date={day}">{fmt_dt(day + "T00:00:00", date_only=True)}</a>'
        for day in dates
    )
    rows = []
    plan_row = state.get("plan_row")
    plan_state = state["plan"].get("approval_state")
    for slot in filtered:
        package_key = quote(str(slot.get("package_key") or "unknown"), safe="")
        row_content = (
            ('<a class="calendar-row" href="/studio/products/{pid}/materials/{aid}">'.format(pid=product.id, aid=slot["artifact_id"])
             if slot.get("artifact_id") else '<a class="calendar-row" href="/studio/products/{pid}/packages/{key}">'.format(pid=product.id, key=package_key))
            + f'<div class="calendar-time">{fmt_dt(slot.get("publish_at"))}</div>'
            + f'<div class="calendar-platform"><span class="platform-mini">{esc(platform_short(slot.get("platform")))}</span><b>{esc(platform_label(slot.get("platform")))}</b></div>'
            + f'<div class="calendar-topic"><b>{esc(slot.get("topic"))}</b><small>{esc(slot.get("objective") or "")}</small></div>'
            + f'<div class="calendar-prepare">Подготовить до<br><b>{fmt_dt(slot.get("prepare_at"))}</b></div>'
            + f'<span class="status {status_class(slot.get("state"))}">{esc(status_label(slot.get("state")))}</span>'
            + '<span class="calendar-arrow">›</span></a>'
        )
        can_move = bool(
            plan_row and plan_state in {"active", "draft"} and slot.get("slot_id")
            and slot.get("state") not in {"scheduled", "published", "delivery_unknown"}
            and not slot.get("telegram_publication_id") and not slot.get("publication_url")
        )
        if can_move:
            dt = parse_dt(slot.get("publish_at"))
            slot_value = esc(dt.strftime("%Y-%m-%dT%H:%M")) if dt else ""
            move_url = (
                f"/studio/products/{product.id}/plans/{plan_row.id}/slots/"
                f"{quote(str(slot['slot_id']), safe='')}/move"
            )
            row_content += (
                '<details class="sg-calendar-move"><summary>🕒 Перенести публикацию</summary>'
                f'<form class="sg-calendar-form" method="post" action="{move_url}">'
                '<label class="sg-field"><span>Новое время · часовой пояс плана</span>'
                f'<input type="datetime-local" name="when" value="{slot_value}" required></label>'
                '<button class="btn secondary" type="submit">Сохранить дату</button>'
                '</form></details>'
            )
        rows.append(f'<div class="sg-calendar-entry">{row_content}</div>')
    body = f"""
    <section class="panel">
      <div class="panel-head">
        <div class="panel-title"><span class="ico">□</span><div><h2>Расписание публикаций</h2>
        <div class="panel-sub">Все слоты текущего редакционного цикла</div></div></div>
        <a class="btn secondary small" href="/studio/products/{product.id}/calendar">Показать все</a>
      </div>
      <div class="calendar-date-tabs"><div class="tabs">{date_tabs}</div></div>
      <div class="calendar-list">{"".join(rows) or '<div class="empty">На выбранную дату публикаций нет.</div>'}</div>
    </section>
    """
    return studio_page_shell(
        product, active="calendar", title="Календарь",
        subtitle="Дата, время, площадка, подготовка и состояние каждой публикации.",
        body=body,
    )


def materials_page(db: Session, product: Product, kind: str = "all", q: str = "", page: int = 1) -> str:
    """Filter before pagination; do not hydrate the entire content library."""
    kinds = {
        "all": MATERIAL_KINDS,
        "article": {"article"},
        "post": {"channel_text", "dzen_brief", "telegram_brief"},
        "video": {"video_script", "short_video_brief", "video_render", "whiteboard_video"},
        "creative": {"creative_brief", "story_brief", "static_image_brief"},
    }
    kind = kind if kind in kinds else "all"
    page = min(10000, max(1, page))
    query_text = q.strip()[:120]
    stmt = select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind.in_(kinds[kind]),
    )
    if query_text:
        literal = (query_text.replace("\\", "\\\\")
                   .replace("%", "\\%").replace("_", "\\_"))
        pattern = "%" + literal + "%"
        stmt = stmt.where(or_(
            Artifact.title.ilike(pattern, escape="\\"),
            Artifact.content.ilike(pattern, escape="\\"),
        ))
    page_size = 24
    rows = list(db.scalars(
        stmt.order_by(Artifact.created_at.desc(), Artifact.id.desc())
        .limit(page_size + 1).offset((page - 1) * page_size)
    ))
    more = len(rows) > page_size
    records = rows[:page_size]
    cards = "".join(material_card(product.id, row) for row in records)
    query_url = "&q=" + quote(query_text) if query_text else ""
    tabs = "".join(
        f'<a class="tab {"active" if kind == value else ""}" '
        f'href="/studio/products/{product.id}/materials?kind={value}{query_url}">{label}</a>'
        for value, label in (
            ("all", "Все"), ("article", "Статьи"), ("post", "Посты"),
            ("video", "Видеосценарии"), ("creative", "Stories и креативы"),
        )
    )
    nav = '<div class="sg-pagination">'
    if page > 1:
        nav += (
            f'<a class="btn secondary" href="/studio/products/{product.id}/materials'
            f'?kind={kind}&page={page-1}{query_url}">← Назад</a>'
        )
    nav += f'<span>Страница {page}</span>'
    if more:
        nav += (
            f'<a class="btn secondary" href="/studio/products/{product.id}/materials'
            f'?kind={kind}&page={page+1}{query_url}">Следующая →</a>'
        )
    nav += '</div>'
    body = f"""
    <section class="panel">
      <div class="panel-head"><div class="panel-title"><span class="ico">▤</span><div>
      <h2>Библиотека</h2><div class="panel-sub">Все статьи, посты, сценарии и визуальные брифы</div>
      </div></div><span class="chip blue">Страница {page}</span></div>
      <form class="library-controls" method="get">
        <input type="hidden" name="kind" value="{esc(kind)}">
        <label class="search"><input name="q" type="search" value="{esc(query_text)}"
          maxlength="120" placeholder="Найти тему или текст материала…" aria-label="Поиск материалов"></label>
        <div class="tabs">{tabs}</div>
        <button class="btn secondary small" type="submit">Найти</button>
      </form>
      <div class="material-grid route-material-grid">{cards or '<div class="empty">По запросу ничего не найдено.</div>'}</div>
      {nav}
    </section>
    """
    return studio_page_shell(
        product, active="materials", title="Материалы",
        subtitle="Поиск, фильтры и удобный просмотр — без загрузки всей библиотеки.",
        body=body,
    )


def material_detail_page(db: Session, product: Product, record: Artifact) -> str:
    value = parse_json(record)
    kind_label = artifact_kind_label(record, value)
    generic_warnings = value.get("quality_warnings") or []
    if record.kind in {"video_script", "short_video_brief"}:
        brief = value.get("production_brief") or value
        shots = "".join(
            '<div class="story-shot">'
            f'<div class="story-shot-head"><time>{esc(shot.get("start_second"))}–{esc(shot.get("end_second"))} сек.</time><b>{esc(shot.get("purpose") or shot.get("shot_type") or "Сцена")}</b></div>'
            f'<p><b>Кадр:</b> {esc(shot.get("visual_description"))}</p>'
            f'<p><b>Камера:</b> {esc(shot.get("camera_and_movement"))}</p>'
            f'<p><b>Действие:</b> {esc(shot.get("action"))}</p>'
            f'<p><b>Текст на экране:</b> {esc(shot.get("on_screen_text"))}</p>'
            f'<p><b>Переход:</b> {esc(shot.get("transition"))}</p>'
            f'<div class="story-prompt"><b>Промпт:</b><br>{esc(shot.get("generation_prompt"))}</div>'
            '</div>'
            for shot in (brief.get("shots") or [])
        )
        content = f"""
        <section class="panel material-detail-card">
          <div class="panel-head"><div class="panel-title"><span class="ico">▶</span><div><h2>Сценарий и раскадровка</h2>
          <div class="panel-sub">{esc(platform_label(brief.get("target_platform")))}</div></div></div></div>
          <div class="material-detail-body">
            <div class="detail-meta"><span class="chip blue">{esc(kind_label)}</span><span class="chip">{esc(record.created_at.strftime("%d.%m.%Y %H:%M") if record.created_at else "")}</span></div>
            <h2>{esc(display_material_title(record, value))}</h2>
            <div id="material-copy" class="article-text"><b>Цель:</b> {esc(brief.get("objective"))}\n\n<b>Озвучка / диалог:</b>\n{esc(brief.get("voiceover_or_dialogue_script"))}\n\n<b>Монтаж и сведение:</b> {esc(brief.get("editing_mix_notes") or brief.get("editing_rhythm"))}</div>
            <div class="storyboard-grid">{shots or '<div class="empty">Раскадровка пока отсутствует.</div>'}</div>
          </div>
        </section>
        """
    elif record.kind == "video_render":
        vid = f"/studio/products/{product.id}/renders/{record.id}/video.mp4"
        cover = f"/studio/products/{product.id}/renders/{record.id}/cover.png"
        content = f"""
        <section class="sg-surface sg-video-file">
          <div class="sg-section-head"><div><h2>Готовый MP4</h2>
          <p>Это действительно созданный видеофайл, а не изображение раскадровки.</p></div></div>
          <video controls preload="metadata" playsinline poster="{cover}?preview=1">
            <source src="{vid}?preview=1" type="video/mp4">
            Ваш браузер не поддерживает воспроизведение MP4.
          </video>
          <div class="sg-inline-actions">
            <a class="btn primary" href="{vid}">Скачать видео MP4</a>
            <a class="btn secondary" href="/studio/products/{product.id}/renders/{record.id}/subtitles.srt">
            Скачать субтитры</a>
            <a class="btn secondary" href="/studio/products/{product.id}/renders/{record.id}/script.txt">
            Скачать сценарий</a>
          </div>
          <p class="sg-muted">Текущий текстовый рендер без озвучки. Для полноценных
          анимационных объяснений нужен отдельный конвейер видеопроизводства.</p>
        </section>
        """
    elif record.kind == "creative_brief":
        frames = value.get("frames") or []
        scenes = "".join(
            '<article class="sg-storyboard-card">'
            f'<div class="sg-storyboard-counter">Кадр {esc(frame.get("number") or index)}'
            + (f' · {esc(frame.get("start_second"))}–{esc(frame.get("end_second"))} с'
               if value.get("format") != "static_image" else '')
            + '</div>'
            f'<div class="sg-storyboard-screen"><span>{esc(frame.get("on_screen_text"))}</span></div>'
            f'<h3>{esc(frame.get("purpose"))}</h3>'
            f'<p>{esc(frame.get("action_or_animation"))}</p>'
            f'<details><summary>Промпт и озвучка</summary><p>{esc(frame.get("visual_prompt"))}</p>'
            f'<p>{esc(frame.get("spoken_text"))}</p></details>'
            '</article>'
            for index, frame in enumerate(frames, 1)
        )
        content = f"""
        <section class="sg-surface">
          <div class="sg-section-head"><div><span class="sg-overline">РАСКАДРОВКА · {esc(value.get('platform'))}</span>
          <h2>{esc(display_material_title(record, value))}</h2><p>Предпросмотр структуры будущего материала. Медиафайлы не создавались.</p></div></div>
          <div class="sg-storyboard-grid">{scenes or '<p>Кадры ещё не подготовлены.</p>'}</div>
          <h3>Текст и рекомендации</h3>
          <div id="material-copy" class="article-text">{esc(artifact_text(record, value))}</div>
        </section>"""
    else:
        text = artifact_text(record, value)
        review_html = ""
        if record.kind == "channel_text":
            warnings = value.get("quality_warnings") or []
            warning_list = "".join(f"<li>{esc(note)}</li>" for note in warnings[:6])
            if warning_list:
                review_html += (
                    '<section class="panel"><h3>Проверить перед публикацией</h3>'
                    '<ul>' + warning_list + '</ul>'
                    '<p>Замечания автоматические; окончательное решение за владельцем.</p>'
                    '</section>'
                )
            if value.get("origin") in {"quick_post", "owner_revision", "owner_manual_revision"}:
                versions = post_history(db, record.id)
                history_links = "".join(
                    f'<a class="btn ghost small" href="/studio/products/{product.id}/materials/{item.id}">'
                    f'Версия {index} · {esc(getattr(item.status, "value", item.status))}</a>'
                    for index, item in enumerate(versions, 1)
                )
                review_html += (
                    '<section class="panel"><h3>История текста</h3>'
                    '<div class="preview-actions">' + history_links + '</div></section>'
                )
        content = f"""
        <section class="panel material-detail-card">
          <div class="panel-head"><div class="panel-title"><span class="ico">▤</span><div><h2>{esc(kind_label)}</h2>
          <div class="panel-sub">Готовый материал</div></div></div></div>
          <div class="material-detail-body">
            <div class="detail-meta"><span class="chip blue">{esc(kind_label)}</span><span class="chip">{esc(record.created_at.strftime("%d.%m.%Y %H:%M") if record.created_at else "")}</span></div>
            <h2>{esc(display_material_title(record, value))}</h2>
            <div class="article-text {'sg-post-preview' if record.kind == 'channel_text' else ''}">
              {f'<div class="sg-native-post-label">Предпросмотр текста · {esc(platform_label(value.get("channel")))}</div>' if record.kind == 'channel_text' else ''}
              <div id="material-copy">{esc(text)}</div>
            </div>
          </div>
        </section>
        {review_html}
        """
        if record.kind == "article":
            start = content.index('<div class="article-text ')
            end = content.index('\n            </div>', start) + len('\n            </div>')
            content = content[:start] + '<div class="sg-rendered-article">' + article_preview_html(text) + '</div><textarea id="material-copy" hidden>' + esc(text) + '</textarea>' + content[end:]
    actions = (
        f'<a class="btn ghost" href="/studio/products/{product.id}/materials">← К материалам</a>'
        '<button class="btn primary" type="button" data-copy="material-copy">Копировать текст</button>'
    )
    if generic_warnings and record.kind != "channel_text":
        content += '<section class="sg-surface" role="alert"><h3>Проверить перед публикацией</h3><ul>' + ''.join(f'<li>{esc(note)}</li>' for note in generic_warnings) + '</ul></section>'
    if record.kind != "channel_text" and value.get("revision_root_id"):
        versions = post_history(db, record.id)
        content += '<section class="sg-surface"><h3>История материала</h3><div class="preview-actions">' + ''.join(
            f'<a class="btn ghost small" href="/studio/products/{product.id}/materials/{item.id}">Версия {index}</a>'
            for index, item in enumerate(versions, 1)) + '</div></section>'
    if record.kind in {"channel_text", "creative_brief", "article", "video_script", "video_render"}:
        status = record.status
        form_actions = ""
        if status in {ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED}:
            edits = editable_fields(record)
            if edits:
                controls = ''.join(f'<label class="sg-field"><span>{esc(label)}</span><textarea name="edit_{index}" rows="6" maxlength="60000">{esc(text)}</textarea></label>' for index, (_, label, text) in enumerate(edits))
                form_actions += (
                    '<details class="sg-manual-editor"><summary>✎ Исправить текст вручную</summary>'
                    f'<form method="post" action="/studio/products/{product.id}/materials/{record.id}/edit">'
                    f'<input type="hidden" name="source_version" value="{record.version}">'
                    f'<input type="hidden" name="source_digest" value="{source_fingerprint(record)}">'
                    f'<label class="sg-field"><span>Заголовок</span><input name="title" value="{esc(record.title)}" maxlength="240" required></label>'
                    + controls + '<p>Сохраняется новая версия. Она потребует отдельного утверждения.</p>'
                    '<button class="btn primary" type="submit">Сохранить новую версию</button></form></details>'
                )
        if status == ArtifactStatus.AWAITING_APPROVAL:
            form_actions += (
                f'<form method="post" action="/studio/products/{product.id}/review/{record.id}/approve">'
                + ('<label><input type="checkbox" name="facts_confirmed" value="yes" required> Я проверил указанные числа, цены и результаты по источникам</label>' if value.get("fact_check_required") else '') +
                '<button class="btn primary" type="submit">✓ Утвердить материал</button></form>'
            )
        if record.kind == "channel_text" and status in {
            ArtifactStatus.AWAITING_APPROVAL, ArtifactStatus.APPROVED
        } and value.get("body") and value.get("channel") in {
            "telegram", "vk", "dzen", "vc_ru", "tenchat"
        }:
            form_actions += (
                f'<form class="sg-inline-form" method="post" action="/studio/products/{product.id}/review/{record.id}/revise">'
                '<label class="sg-field"><span>Что исправить в этом посте?</span>'
                '<textarea name="feedback" required minlength="8" maxlength="1800" rows="3" '
                'placeholder="Например: сократи начало, приведи понятный пример, убери штампы"></textarea>'
                '<small>ИИ создаст новую версию. Предыдущий текст сохранится.</small></label>'
                '<button type="submit" class="btn secondary">✦ Попросить ИИ доработать</button></form>'
            )
        if status == ArtifactStatus.APPROVED and value.get("channel") == "telegram":
            form_actions += (
                f'<div class="sg-inline-form"><a class="btn secondary" '
                f'href="/studio/products/{product.id}/channels?post_id={record.id}">'
                '↗ Запланировать публикацию в Telegram</a></div>'
            )
        status_text = {
            ArtifactStatus.APPROVED: "Согласован",
            ArtifactStatus.AWAITING_APPROVAL: "Ожидает вашего решения",
            ArtifactStatus.REJECTED: "Сохранён в истории",
        }.get(status, getattr(status, "value", str(status)))
        content += (
            '<section class="sg-surface">'
            '<div class="sg-detail-steps"><span class="done">Создан</span>'
            f'<span class="{"current" if status == ArtifactStatus.AWAITING_APPROVAL else "done"}">Проверка</span>'
            '<span>Публикация отдельно</span></div>'
            f'<h2>{esc(status_text)}</h2>'
            '<p>Проверяйте конкретную версию. Утверждение ещё не означает публикацию.</p>'
            + form_actions + '</section>'
        )
    return studio_page_shell(
        product, active="materials", title=display_material_title(record, value),
        subtitle=f"{kind_label} · просмотр, проверка и версии.",
        body=content, actions=actions,
    )


def package_detail_page(db: Session, product: Product, package_key: str) -> str:
    state = studio_state(db, product)
    group = state["groups"].get(package_key)
    if not group:
        raise HTTPException(404, "Package not found")
    plan_row = state["plan_row"]
    plan = state["plan"]
    task_ids = {slot.get("task_id") for slot in group if slot.get("task_id")}
    materials: list[Artifact] = []
    for task_id in task_ids:
        materials.extend([a for a in state["by_task"].get(str(task_id), []) if a.kind in MATERIAL_KINDS])
    topic = group[0].get("topic") or package_key
    slot_rows = "".join(
        '<div class="package-slot-row">'
        f'<div><span class="platform-mini">{esc(platform_short(slot.get("platform")))}</span><b>{esc(platform_label(slot.get("platform")))}</b></div>'
        f'<div><small>Публикация</small><b>{fmt_dt(slot.get("publish_at"))}</b></div>'
        f'<div><small>Подготовить до</small><b>{fmt_dt(slot.get("prepare_at"))}</b></div>'
        f'<span class="status {status_class(slot.get("state"))}">{esc(status_label(slot.get("state")))}</span>'
        '</div>'
        for slot in sorted(group, key=lambda x: x.get("publish_at") or "")
    )
    material_cards = "".join(material_card(product.id, record) for record in materials)
    queue = ""
    if plan_row and plan.get("approval_state") == "active" and (
        not task_ids or state_for_group(group) in {"failed", "cancelled"}
    ):
        queue = (
            f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/packages/{quote(package_key, safe="")}/queue">'
            f'<button class="btn primary" type="submit">{"Повторить подготовку" if task_ids else "Подготовить пакет сейчас"}</button></form>'
        )
    body = f"""
    <section class="panel">
      <div class="panel-head"><div class="panel-title"><span class="ico">▱</span><div><h2>{esc(topic)}</h2>
      <div class="panel-sub">{esc(group[0].get("objective") or group[0].get("audience") or "")}</div></div></div>
      <span class="status {status_class(state_for_group(group))}">{esc(status_label(state_for_group(group)))}</span></div>
      <div class="package-detail-slots">{slot_rows}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><div class="panel-title"><span class="ico">▤</span><div><h2>Материалы пакета</h2>
      <div class="panel-sub">Все адаптации, уже созданные для этой темы</div></div></div><span class="chip blue">{len(materials)}</span></div>
      <div class="material-grid route-material-grid">{material_cards or '<div class="empty">Материалы ещё не созданы.</div>'}</div>
    </section>
    """
    return studio_page_shell(
        product, active="plan", title=topic,
        subtitle="Контент-пакет: расписание по площадкам и связанные материалы.",
        body=body, actions=queue,
    )


def analytics_page(db: Session, product: Product) -> str:
    state = studio_state(db, product)
    slots = state["slots"]
    tasks = state["tasks"]
    materials = state["materials"]
    state_counts: dict[str, int] = defaultdict(int)
    for slot in slots:
        state_counts[str(slot.get("state") or "planned")] += 1
    failed_tasks = sum(1 for task in tasks if getattr(task.status, "value", str(task.status)) == "failed")
    completed_tasks = sum(1 for task in tasks if getattr(task.status, "value", str(task.status)) == "completed")
    body = f"""
    <section class="analytics-grid">
      <div class="kpi blue"><span class="kpi-icon">▱</span><div><strong>{len(state["groups"])}</strong><small>пакетов в текущем плане</small></div></div>
      <div class="kpi green"><span class="kpi-icon">▤</span><div><strong>{len(materials)}</strong><small>готовых артефактов в библиотеке</small></div></div>
      <div class="kpi purple"><span class="kpi-icon">✓</span><div><strong>{completed_tasks}</strong><small>завершённых задач</small></div></div>
      <div class="kpi orange"><span class="kpi-icon">!</span><div><strong>{failed_tasks}</strong><small>задач с ошибкой</small></div></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div class="panel-title"><span class="ico">⌁</span><div><h2>Статусы публикационных слотов</h2>
      <div class="panel-sub">Реальные данные текущего плана</div></div></div></div>
      <div class="analytics-statuses">{"".join(f'<div><span class="status {status_class(k)}">{esc(status_label(k))}</span><b>{v}</b></div>' for k,v in sorted(state_counts.items())) or '<div class="empty">Слотов нет.</div>'}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><div class="panel-title"><span class="ico">↗</span><div><h2>Публикационная аналитика</h2>
      <div class="panel-sub">Просмотры, переходы, обращения и клиенты появятся после подключения API площадок и атрибуции</div></div></div></div>
      <div class="empty">Сейчас Growth Studio показывает только подтверждённые внутренние данные и не придумывает внешние метрики.</div>
    </section>
    """
    return studio_page_shell(
        product, active="analytics", title="Аналитика",
        subtitle="Состояние контент-производства и место для будущих метрик публикаций.",
        body=body,
    )

def product_selector(products: list[Product], *, can_add: bool = False) -> str:
    add_card = ('<a class="product-link" href="/studio/new"><strong>＋ Добавить компанию</strong>'
                '<small>Описание, сайт и проверка профиля →</small></a>') if can_add else ""
    cards = "".join(
        f'<a class="product-link" href="/studio/products/{p.id}"><strong>{esc(p.name)}</strong><small>{esc(p.slug)} · открыть Growth Studio →</small></a>'
        for p in products
    )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Growth Studio</title><link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
  <link rel="stylesheet" href="/studio/assets/studio.css?v=10"></head>
<body><main class="product-home">
<section class="panel hero">{brand_html()}<div class="eyebrow" style="justify-content:center;margin-top:18px">GROWTH STUDIO <span class="beta">BETA</span></div>
<h1>Выберите проект</h1><p class="subtitle">Контент-план, готовые материалы и сценарии собраны в одном интерфейсе.</p></section>
<div class="product-list">{cards or '<div class="empty">Проекты ещё не добавлены.</div>'}{add_card}</div>
</main></body></html>"""


@router.get("/studio/assets/servgo.svg")
def studio_official_logo():
    response = Response(SERVGO_FAVICON_SVG, media_type="image/svg+xml")
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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
def studio(request: Request, choose: bool = False, db: Session = Depends(get_db)):
    query = select(Product)
    allowed = getattr(request.state, "studio_product_ids", None)
    if allowed is not None:
        query = query.where(Product.id.in_([UUID(value) for value in allowed]))
    products = list(db.scalars(query.order_by(Product.name).limit(100)))
    preferred = next((p for p in products if p.slug == "servgo"), None)
    if preferred is not None and not choose:
        return RedirectResponse(f"/studio/products/{preferred.id}", status_code=302)
    response = HTMLResponse(product_selector(products, can_add=allowed is None))
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



@router.get("/studio/products/{product_id}/plan", response_class=HTMLResponse)
def studio_plan(product_id: UUID, status: str = "all", view: str = "current", db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    if status not in {"all", "ready", "failed"}:
        status = "all"
    response = HTMLResponse(plan_page(db, product, status=status, view=view))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}/calendar", response_class=HTMLResponse)
def studio_calendar(product_id: UUID, date: str | None = None, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    response = HTMLResponse(calendar_page(db, product, selected_date=date))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}/materials", response_class=HTMLResponse)
def studio_materials(product_id: UUID, kind: str = "all", q: str = "", page: int = 1,
                     db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    if kind not in {"all", "article", "post", "video", "creative"}:
        kind = "all"
    response = HTMLResponse(materials_page(db, product, kind=kind, q=q, page=page))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}/materials/{artifact_id}", response_class=HTMLResponse)
def studio_material_detail(product_id: UUID, artifact_id: UUID, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    record = db.get(Artifact, artifact_id)
    if record is None or record.product_id != product.id or record.kind not in MATERIAL_KINDS:
        raise HTTPException(404, "Material not found")
    if record.kind == "whiteboard_video":
        if not record.task_id:
            raise HTTPException(404, "Исходное задание видео не найдено")
        return RedirectResponse(
            f"/studio/products/{product_id}/whiteboard/{record.task_id}", status_code=303,
        )
    response = HTMLResponse(material_detail_page(db, product, record))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}/renders/{render_id}/{filename}")
def studio_rendered_asset(product_id: UUID, render_id: UUID, filename: str,
                          preview: bool = False, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    if filename not in {"video.mp4", "cover.png", "script.txt", "subtitles.srt"}:
        raise HTTPException(404, "Файл не найден")
    video = db.scalar(select(Artifact).where(
        Artifact.id == render_id, Artifact.product_id == product_id,
        Artifact.kind == "video_render",
    ))
    if video is None:
        raise HTTPException(404, "Видеофайл не найден")
    metadata = parse_json(video)
    try:
        script_id = UUID(str(metadata["script_id"]))
        path = asset_path(get_settings().products_root, product_id, script_id, filename)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(404, "Некорректные данные видео") from None
    if not path.is_file():
        raise HTTPException(404, "Файл ещё не сохранён или недоступен")
    types = {"video.mp4": "video/mp4", "cover.png": "image/png",
             "script.txt": "text/plain; charset=utf-8",
             "subtitles.srt": "text/plain; charset=utf-8"}
    response = FileResponse(
        str(path), media_type=types[filename], filename=filename,
        content_disposition_type="inline" if preview else "attachment",
    )
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.get("/studio/products/{product_id}/packages/{package_key}", response_class=HTMLResponse)
def studio_package_detail(product_id: UUID, package_key: str, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    response = HTMLResponse(package_detail_page(db, product, package_key))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/studio/products/{product_id}/analytics", response_class=HTMLResponse)
def studio_analytics(product_id: UUID, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    response = HTMLResponse(analytics_page(db, product))
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
    return RedirectResponse(f"/studio/products/{product_id}/plan", status_code=303)


@router.post("/studio/products/{product_id}/plans/{plan_id}/pause")
def studio_pause(product_id: UUID, plan_id: UUID, db: Session = Depends(get_db)):
    row = db.get(Artifact, plan_id)
    if row is None or row.product_id != product_id:
        raise HTTPException(404, "Plan not found")
    try:
        pause_plan(db, plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/plan", status_code=303)


@router.post("/studio/products/{product_id}/plans/{plan_id}/packages/{package_key}/queue")
def studio_queue_package(product_id: UUID, plan_id: UUID, package_key: str, db: Session = Depends(get_db)):
    # Match the lock order of tick_calendar/approve_plan: company, then editorial plan.
    product = db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    if product is None:
        raise HTTPException(404, "Product not found")
    row = db.scalar(
        select(Artifact).where(
            Artifact.id == plan_id, Artifact.product_id == product_id, Artifact.kind == "editorial_plan"
        ).with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Plan not found")
    plan = parse_json(row)
    if plan.get("approval_state") != "active":
        raise HTTPException(409, "Approve the editorial plan before queueing its packages")
    slots = [slot for slot in (plan.get("slots") or []) if slot.get("package_key") == package_key]
    if not slots:
        raise HTTPException(404, "Package not found")

    old_task_ids = {slot.get("task_id") for slot in slots if slot.get("task_id")}
    old_tasks = [db.get(Task, UUID(task_id)) for task_id in old_task_ids]
    # Only a failed/cancelled task can be restarted by the owner. Never queue a
    # second production while another is running, or overwrite completed work.
    if old_task_ids and any(
        task is None or task.product_id != product_id or
        task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}
        for task in old_tasks
    ):
        raise HTTPException(409, "Пакет уже запущен или подготовлен; повтор не требуется")

    topic = slots[0].get("topic")
    audience = slots[0].get("audience")
    objective = slots[0].get("objective") or ""
    channels = ", ".join(sorted({slot.get("platform", "") for slot in slots}))
    task = queue_content(
        db, product_id, topic, commit=False,
        dedupe_key=f"calendar:{plan_id}:{package_key}",
        audience=audience,
        editorial_brief=(
            f"Цель пакета: {objective}\n"
            f"Площадки: {channels}\n"
            "План подтверждает только подготовку; публикация вручную."
        ),
    )
    for slot in slots:
        slot["task_id"] = str(task.id)
        slot["state"] = "preparing" if task.status == TaskStatus.RUNNING else "queued"
    row.content = json.dumps(plan, ensure_ascii=False)
    db.commit()
    return RedirectResponse(f"/studio/products/{product_id}/packages/{quote(package_key, safe='')}", status_code=303)


# Growth Studio v5: the same authenticated owner workflow on the website,
# without requiring Telegram chat commands. These POSTs are subject to
# require_studio_auth and require_studio_csrf on the entire router.

async def _studio_form(request: Request) -> dict[str, str]:
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 1000000:
            raise HTTPException(413, "Слишком большой запрос")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        raise HTTPException(415, "Ожидается форма")
    try:
        parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True,
                          max_num_fields=80, strict_parsing=False)
    except (UnicodeError, ValueError):
        raise HTTPException(400, "Некорректная форма") from None
    return {k: v[-1].strip() for k, v in parsed.items() if v}


def _owner_product(db: Session, product_id: UUID) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Компания не найдена")
    return product


@router.post("/studio/products/{product_id}/materials/{artifact_id}/edit")
async def studio_manual_edit(product_id: UUID, artifact_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        revised = save_manual_edit(db, product_id, artifact_id, fields)
    except (LookupError, ValueError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/materials/{revised.id}", status_code=303)


@router.get("/studio/products/{product_id}/create", response_class=HTMLResponse)
def studio_create_page(product_id: UUID, created: bool = False, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    html_body = create_content_html(product.id, created=created)
    # Tasks have a dedicated persistent workspace; never duplicate a raw
    # technical job log below the creative form.
    html_body += ('<p class="sg-create-work-link"><a href="/studio/products/'
                  f'{product.id}/work">Посмотреть все запущенные задания →</a></p>')
    response = HTMLResponse(studio_page_shell(
        product, active="create", title="Создать материал",
        subtitle="Одна задача, одна площадка и понятный результат — без лишних настроек.",
        body=html_body,
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/studio/products/{product_id}/create")
async def studio_create_submit(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    chosen = fields.get("format", "post")
    topic = fields.get("topic", "")
    audience = fields.get("audience") or None
    brief = fields.get("editorial_brief") or None
    try:
        if chosen == "package":
            task = queue_content(db, product.id, topic, audience=audience, editorial_brief=brief)
        elif chosen == "post":
            task = queue_content(db, product.id, topic, format="post",
                          channel=fields.get("channel"), audience=audience, editorial_brief=brief)
        elif chosen in {"short_video", "story", "static_image"}:
            command = CreativeRequest(mode="creative", product_id=product.id, topic=topic,
                                      format=chosen, platform=fields.get("channel") or "",
                                      audience=audience, editorial_brief=brief)
            task = queue_creative(db, command)
        else:
            raise ValueError("Неизвестный формат")
    except (ValueError, LookupError) as exc:
        db.rollback()
        return HTMLResponse(studio_page_shell(
            product, active="create", title="Создать материал", subtitle="Проверьте поля задания.",
            body=create_content_html(product.id, fields=fields, error="Проверьте формат, площадку и длину полей: тема 3–300, аудитория до 300, требования до 3500 символов."),
        ), status_code=422, headers={"Cache-Control": "no-store"})
    return RedirectResponse(f"/studio/products/{product.id}/tasks/{task.id}", status_code=303)


@router.get("/studio/products/{product_id}/work", response_class=HTMLResponse)
def studio_work_page(product_id: UUID, tab: str = "all", db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    tab = tab if tab in {"all", "active", "approval", "ready", "problems"} else "all"
    html_body = activity_html(product.id, work_items(db, product.id), tab=tab)
    result = HTMLResponse(studio_page_shell(
        product, active="work", title="В работе",
        subtitle="Здесь видно, что создаётся, что готово и что требует внимания.",
        body=html_body,
    ))
    result.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/studio/products/{product_id}/activity.json")
def studio_activity_json(product_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    jobs = work_items(db, product.id, limit=45)
    notes = notice_items(db, product.id, limit=10)
    response = JSONResponse({
        "jobs": jobs,
        "notices": notes,
        "attention_count": len(notes),
        "active_count": sum(1 for job in jobs if job["state"] in {"queued", "running"}),
    })
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get("/studio/products/{product_id}/tasks/{task_id}", response_class=HTMLResponse)
def studio_task_page(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    task = db.scalar(select(Task).where(Task.id == task_id, Task.product_id == product_id))
    if task is None:
        raise HTTPException(404, "Задание не найдено")
    if task.assigned_agent == "whiteboard_renderer":
        return RedirectResponse(f"/studio/products/{product_id}/whiteboard/{task_id}", status_code=303)
    runs = list(db.scalars(select(AgentRun).where(
        AgentRun.task_id == task.id,
    ).order_by(AgentRun.started_at, AgentRun.id)))
    results = list(db.scalars(select(Artifact).where(
        Artifact.task_id == task.id,
        Artifact.product_id == product_id,
        Artifact.kind.in_(RESULT_KINDS),
    ).order_by(Artifact.created_at, Artifact.id)))
    request = db.scalar(select(Artifact).where(
        Artifact.task_id == task.id, Artifact.product_id == product_id,
        Artifact.kind.in_(["content_request", "creative_request", "edit_request"]),
    ).order_by(Artifact.created_at.desc()).limit(1))
    body = task_detail_html(product.id, task, runs, results, request=request)
    response = HTMLResponse(studio_page_shell(
        product, active="work", title="Создание материала",
        subtitle="Настоящий статус задания, этапы ИИ-команды и готовые результаты.",
        body=body,
    ))
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.post("/studio/products/{product_id}/tasks/{task_id}/retry")
def studio_retry_task(product_id: UUID, task_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    task = db.scalar(select(Task).where(Task.id == task_id, Task.product_id == product_id).with_for_update().execution_options(populate_existing=True))
    if task is None:
        raise HTTPException(404, "Задание не найдено")
    if task.assigned_agent not in {"content_factory", "content_creative", "content_editor"}:
        raise HTTPException(409, "Это задание нельзя повторить из страницы создания")
    if task.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
        raise HTTPException(409, "Повторить можно только остановленное задание")
    duplicate = db.scalar(select(Task.id).where(Task.product_id == product_id,
        Task.title == task.title, Task.assigned_agent == task.assigned_agent,
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])))
    if duplicate:
        raise HTTPException(409, "Такое задание уже выполняется")
    partial = db.scalar(select(Artifact.id).where(Artifact.task_id == task_id, Artifact.kind.in_(MATERIAL_KINDS)))
    if partial:
        raise HTTPException(409, "У задания уже есть материалы. Проверьте их перед новым запуском.")
    task.status = TaskStatus.QUEUED
    db.commit()
    return RedirectResponse(f"/studio/products/{product_id}/tasks/{task_id}", status_code=303)


@router.get("/studio/products/{product_id}/review", response_class=HTMLResponse)
def studio_review_page(product_id: UUID, kind: str = "all", db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    if kind not in {"all", "text", "creative"}:
        kind = "all"
    records = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind.in_(["channel_text", "creative_brief", "article", "video_script", "video_render"]),
        Artifact.status == ArtifactStatus.AWAITING_APPROVAL,
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(250)))
    response = HTMLResponse(studio_page_shell(
        product, active="review", title="На проверке",
        subtitle="Только готовые материалы, для которых необходимо ваше решение.",
        body=review_html(product.id, records, filter_name=kind),
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/studio/products/{product_id}/review/{artifact_id}/approve")
async def studio_material_approve(product_id: UUID, artifact_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    db.scalar(select(Product).where(Product.id == product_id).with_for_update())
    row = db.scalar(select(Artifact).where(
        Artifact.id == artifact_id, Artifact.product_id == product_id,
        Artifact.kind.in_(["channel_text", "creative_brief", "article", "video_script", "video_render"])
    ).with_for_update().execution_options(populate_existing=True))
    if row is None:
        raise HTTPException(404, "Материал не найден")
    if row.status == ArtifactStatus.APPROVED:
        return RedirectResponse(f"/studio/products/{product_id}/materials/{artifact_id}", status_code=303)
    if row.status != ArtifactStatus.AWAITING_APPROVAL:
        raise HTTPException(409, "Согласовать эту версию уже нельзя")
    if parse_json(row).get("fact_check_required"):
        fields = await _studio_form(request)
        if fields.get("facts_confirmed") != "yes":
            raise HTTPException(422, "Сначала проверьте числа, цены и результаты в полном просмотре материала")
    if row.kind == "video_render":
        metadata = parse_json(row)
        try:
            script_id = UUID(str(metadata["script_id"]))
            video_file = asset_path(get_settings().products_root, product_id, script_id, "video.mp4")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, "У видео нет корректных исходных данных") from None
        if not video_file.is_file():
            raise HTTPException(409, "Видеофайл ещё не создан или недоступен")
    if row.kind == "channel_text":
        source = parse_json(row)
        if not source.get("body"):
            raise HTTPException(409, "Текст материала отсутствует")
        if source.get("channel") == "telegram":
            try:
                validate_telegram_text(source["body"])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        pending = db.scalar(select(Task.id).where(
            Task.product_id == product_id,
            Task.assigned_agent == "content_editor",
            Task.title == "EDIT: " + str(row.id),
            Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING])
        ).limit(1))
        if pending:
            raise HTTPException(409, "Доработка ещё выполняется — подтвердите новую версию")
    row.status = ArtifactStatus.APPROVED
    row.version += 1
    db.commit()
    return RedirectResponse(f"/studio/products/{product_id}/materials/{artifact_id}", status_code=303)


@router.post("/studio/products/{product_id}/review/{artifact_id}/revise")
async def studio_material_revise(product_id: UUID, artifact_id: UUID,
                                 request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    row = db.get(Artifact, artifact_id)
    if row is None or row.product_id != product.id or row.kind != "channel_text":
        raise HTTPException(404, "Пост не найден")
    fields = await _studio_form(request)
    try:
        task = queue_post_edit(db, artifact_id, fields.get("feedback", ""))
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/tasks/{task.id}", status_code=303)


@router.get("/studio/products/{product_id}/channels", response_class=HTMLResponse)
def studio_channels(product_id: UUID, added: bool = False, scheduled: bool = False,
                    post_id: UUID | None = None, page: int = 1, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    connections = connected_channels(db, product.id)
    page = max(1, page)
    total = db.scalar(select(func.count()).select_from(Publication).where(
        Publication.product_id == product.id, Publication.channel == "telegram")) or 0
    page = min(page, max(1, (total + 29) // 30))
    publications = list(db.scalars(select(Publication).where(
        Publication.product_id == product.id,
        Publication.channel == "telegram",
    ).order_by(Publication.created_at.desc(), Publication.id.desc()).offset((page - 1) * 30).limit(30)))
    candidate_posts = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind == "channel_text",
        Artifact.status == ArtifactStatus.APPROVED,
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    approved_posts = [row for row in candidate_posts if parse_json(row).get("channel") == "telegram"]
    intents = list(db.scalars(select(Artifact).where(Artifact.product_id == product.id,
        Artifact.kind == "telegram_publish_intent")))
    metas = {str(parse_json(row).get("publication_id")): parse_json(row) for row in intents}
    details = {}
    for pub in publications:
        meta = metas.get(str(pub.id), {})
        source = db.get(Artifact, pub.artifact_id)
        connection = next((row for row in connections if str(row.id) == str(meta.get("connection_id"))), None)
        details[str(pub.id)] = {"title": source.title if source else "Материал", "channel": connection.title if connection else "Telegram",
                                "when": fmt_dt(parse_dt(meta.get("scheduled_at")).astimezone(ZoneInfo("Europe/Moscow")).isoformat()) + " · Москва" if parse_dt(meta.get("scheduled_at")) else "Дата не указана"}
    response = HTMLResponse(studio_page_shell(
        product, active="channels", title="Каналы и публикации",
        subtitle="Подключения, назначенные отправки и реальные результаты Telegram.",
        body=channels_html(product.id, connections, publications, approved_posts,
                           added=added, scheduled=scheduled, selected_post=post_id,
                           autopost_enabled=get_settings().content_telegram_autopost_enabled,
                           publication_details=details, publication_page=page, publication_total=total),
    ))
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/studio/products/{product_id}/channels/connect")
async def studio_connect_channel(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    from app.telegram_control_bot import TelegramAPI
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise HTTPException(409, "Telegram-бот ещё не настроен на сервере")
    try:
        api = TelegramAPI(settings.telegram_bot_token, settings.telegram_proxy_url)
        verified = await verify_channel(api, fields.get("channel", ""))
        save_verified_channel(db, product_id, verified)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception:
        raise HTTPException(502, "Не удалось проверить канал. Проверьте права бота и повторите.") from None
    return RedirectResponse(f"/studio/products/{product_id}/channels?added=1", status_code=303)


@router.post("/studio/products/{product_id}/publishing/schedule")
async def studio_schedule_post(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    try:
        post_id = UUID(fields.get("post_id", ""))
        connection_id = UUID(fields.get("connection_id", ""))
        local = datetime.fromisoformat(fields.get("when", ""))
        if local.tzinfo is not None:
            raise ValueError("Выберите местное время Москвы, без смещения")
        when = local.replace(tzinfo=ZoneInfo("Europe/Moscow"))
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
        schedule_post(db, post_id, connection_id, when, plan_id=plan_id, slot_id=slot_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(422, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/channels?scheduled=1", status_code=303)


@router.post("/studio/products/{product_id}/publishing/{publication_id}/reconcile")
async def studio_reconcile_publication(product_id: UUID, publication_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    fields = await _studio_form(request)
    if fields.get("confirmed") != "yes":
        raise HTTPException(422, "Подтвердите ручную проверку канала")
    try:
        message_id = int(fields["message_id"]) if fields.get("message_id") else None
        reconcile_publication(db, publication_id, product_id=product_id,
                              outcome=fields.get("outcome", ""), message_id=message_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/channels", status_code=303)


@router.post("/studio/products/{product_id}/publishing/{publication_id}/cancel")
def studio_cancel_publication(product_id: UUID, publication_id: UUID, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    publication = db.get(Publication, publication_id)
    if publication is None or publication.product_id != product_id:
        raise HTTPException(404, "Публикация не найдена")
    try:
        cancel_scheduled_post(db, publication_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/channels", status_code=303)


@router.post("/studio/products/{product_id}/plans/{plan_id}/slots/{slot_id}/move")
async def studio_slot_move(product_id: UUID, plan_id: UUID, slot_id: str,
                           request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    plan = db.get(Artifact, plan_id)
    if plan is None or plan.product_id != product_id or plan.kind != "editorial_plan":
        raise HTTPException(404, "Контент-план не найден")
    fields = await _studio_form(request)
    try:
        local = datetime.fromisoformat(fields.get("when", ""))
        if local.tzinfo is not None:
            raise ValueError("Выберите дату в часовом поясе плана")
        current = parse_json(plan)
        when = local.replace(tzinfo=ZoneInfo(current.get("timezone") or "Europe/Moscow"))
        reschedule_slot(db, plan_id, slot_id, when)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/calendar", status_code=303)


@router.post("/studio/products/{product_id}/plan/generate")
async def studio_generate_plan(product_id: UUID, request: Request, db: Session = Depends(get_db)):
    _owner_product(db, product_id)
    values = await _studio_form(request)
    text = values.get("requirements", "").strip()
    try:
        if text:
            save_preferences(db, product_id, text)
        queue_plan(db, product_id)
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc)) from None
    return RedirectResponse(f"/studio/products/{product_id}/plan", status_code=303)
