"""Owner-facing visual content workspace.

The production API is currently loopback-only and has no general web authentication.
Do not expose these routes publicly until an authenticated HTTPS boundary is added.
"""
from __future__ import annotations

import html
import json
from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.calendar import PLATFORMS, approve_plan, pause_plan
from app.content.service import queue_content
from app.core.config import get_settings
from app.db.session import get_db
from app.models.domain import Artifact, Product, Task

router = APIRouter()


CSS = """
:root{font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111827;background:#f7f7f8}
*{box-sizing:border-box}body{margin:0}a{color:inherit}.shell{max-width:1320px;margin:auto;padding:24px}
.top{display:flex;gap:16px;align-items:center;justify-content:space-between;flex-wrap:wrap;margin-bottom:24px}
h1,h2,h3{margin:.2em 0}.muted{color:#6b7280}.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}
.stat,.card,.panel{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:16px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.stat b{display:block;font-size:1.7rem}.chips{display:flex;flex-wrap:wrap;gap:6px}.chip{font-size:.78rem;border-radius:999px;padding:5px 9px;background:#f3f4f6}
.state{font-weight:700}.planned{color:#4b5563}.queued,.preparing{color:#92400e}.ready_for_review{color:#166534}.failed,.missed{color:#991b1b}
.calendar{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:8px}.day{min-height:120px;background:#fff;border:1px solid #e5e7eb;border-radius:12px;padding:10px}.slot{margin-top:7px;padding:8px;border-left:3px solid #9ca3af;background:#f9fafb;border-radius:7px;font-size:.84rem}
.packages{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.package h3{margin-bottom:6px}.asset{border-top:1px solid #eee;padding:12px 0}.asset:first-child{border-top:0}
pre{white-space:pre-wrap;word-break:break-word;background:#f9fafb;border-radius:10px;padding:12px;overflow:auto}.storyboard{display:grid;gap:8px}.shot{background:#f9fafb;border-radius:10px;padding:10px}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:0;border-radius:10px;padding:9px 12px;background:#111827;color:#fff;cursor:pointer}.btn.secondary{background:#e5e7eb;color:#111827}.danger{background:#7f1d1d}
details summary{cursor:pointer;font-weight:700}.product-list{display:grid;gap:10px}.product-link{text-decoration:none}
@media(max-width:900px){.grid{grid-template-columns:repeat(2,1fr)}.calendar{grid-template-columns:1fr}.day{min-height:auto}.packages{grid-template-columns:1fr}}
@media(max-width:520px){.shell{padding:14px}.grid{grid-template-columns:1fr}.stat b{font-size:1.4rem}}
"""


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
        select(Artifact).where(Artifact.product_id == product_id, Artifact.kind == "editorial_plan")
        .order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(10)
    ))
    parsed = []
    for row in rows:
        value = parse_json(row)
        if value:
            parsed.append((row, value))
    parsed.sort(key=lambda item: item[1].get("generated_at", ""), reverse=True)
    return parsed[0] if parsed else (None, None)


def platform_label(key: str) -> str:
    return PLATFORMS.get(key, (key, ""))[0]


def artifact_html(record: Artifact) -> str:
    value = parse_json(record)
    title = esc(record.title)
    if not value:
        return f'<div class="asset"><h4>{title}</h4><p class="muted">Не удалось прочитать этот артефакт.</p></div>'
    if record.kind == "article":
        body = value.get("full_article") or value.get("reader_article") or record.content
        return f'<div class="asset"><h4>{title}</h4><pre>{esc(body)}</pre></div>'
    if record.kind == "channel_text":
        label = platform_label(value.get("channel", ""))
        return f'<div class="asset"><h4>{esc(label)} · {title}</h4><pre>{esc(value.get("body",""))}</pre></div>'
    if record.kind == "video_script":
        brief = value.get("production_brief") or {}
        shots = []
        for shot in brief.get("shots") or []:
            shots.append(
                '<div class="shot">'
                f'<b>{esc(shot.get("start_second"))}–{esc(shot.get("end_second"))} сек.</b>'
                f'<div>{esc(shot.get("visual_description"))}</div>'
                f'<div><b>Камера:</b> {esc(shot.get("camera_and_movement"))}</div>'
                f'<div><b>На экране:</b> {esc(shot.get("on_screen_text"))}</div>'
                f'<div><b>Переход:</b> {esc(shot.get("transition"))}</div>'
                f'<div><b>Промпт:</b> {esc(shot.get("generation_prompt"))}</div>'
                '</div>'
            )
        hooks = "".join(f"<li>{esc(h)}</li>" for h in (brief.get("hook_variants") or []))
        return (
            f'<div class="asset"><h4>{esc(brief.get("target_platform","short_video"))} · {title}</h4>'
            f'<p><b>Цель:</b> {esc(brief.get("objective"))}</p>'
            f'<p><b>Хуки:</b></p><ul>{hooks}</ul>'
            f'<p><b>Текст:</b> {esc(brief.get("voiceover_or_dialogue_script"))}</p>'
            f'<div class="storyboard">{"".join(shots)}</div>'
            f'<p><b>Монтаж/сведение:</b> {esc(brief.get("editing_mix_notes") or brief.get("editing_rhythm"))}</p>'
            f'<p><b>Обложка:</b> {esc(brief.get("cover_frame"))}</p></div>'
        )
    if record.kind == "content_distribution":
        return f'<div class="asset"><h4>{title}</h4><pre>{esc(json.dumps(value,ensure_ascii=False,indent=2))}</pre></div>'
    return f'<div class="asset"><h4>{title}</h4><pre>{esc(json.dumps(value,ensure_ascii=False,indent=2))}</pre></div>'


def product_dashboard(db: Session, product: Product) -> str:
    plan_row, plan = latest_plan(db, product.id)
    tasks = list(db.scalars(
        select(Task).where(Task.product_id == product.id, Task.assigned_agent.in_(["content_factory","editorial_planner"]))
        .order_by(Task.created_at.desc()).limit(100)
    ))
    artifacts = list(db.scalars(
        select(Artifact).where(Artifact.product_id == product.id)
        .order_by(Artifact.created_at.desc(), Artifact.id.desc()).limit(500)
    ))
    by_task = defaultdict(list)
    for artifact in artifacts:
        if artifact.task_id:
            by_task[str(artifact.task_id)].append(artifact)

    slots = (plan or {}).get("slots") or []
    groups = defaultdict(list)
    for slot in slots:
        groups[slot.get("package_key") or "unknown"].append(slot)

    ready = sum(1 for s in slots if s.get("state") == "ready_for_review")
    failed = sum(1 for s in slots if s.get("state") == "failed")
    next_slots = sorted(
        [s for s in slots if s.get("state") in {"planned","queued","preparing"} and s.get("publish_at")],
        key=lambda s: s.get("publish_at")
    )
    next_due = next_slots[0].get("publish_at") if next_slots else "—"
    channels = sorted({s.get("platform") for s in slots if s.get("platform")})

    stats = f"""
    <div class="grid">
      <div class="stat"><span class="muted">Следующая публикация</span><b>{esc(next_due)}</b></div>
      <div class="stat"><span class="muted">Пакетов в спринте</span><b>{len(groups)}</b></div>
      <div class="stat"><span class="muted">Готово к проверке</span><b>{ready}</b></div>
      <div class="stat"><span class="muted">Ошибок подготовки</span><b>{failed}</b></div>
    </div>
    """

    action_html = ""
    if plan_row and plan:
        if plan.get("approval_state") == "draft":
            action_html += f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/approve"><button class="btn">Подтвердить план</button></form>'
        elif plan.get("approval_state") == "active":
            action_html += f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/pause"><button class="btn danger">Пауза</button></form>'

    calendar_items = []
    for slot in sorted(slots, key=lambda s: s.get("publish_at") or ""):
        state = esc(slot.get("state") or "planned")
        calendar_items.append(
            '<div class="slot">'
            f'<div><b>{esc((slot.get("publish_at") or "")[5:16].replace("T"," "))}</b> · {esc(platform_label(slot.get("platform","")))}</div>'
            f'<div>{esc(slot.get("topic"))}</div>'
            f'<div class="state {state}">{state}</div>'
            f'<div class="muted">готовить: {esc((slot.get("prepare_at") or "")[5:16].replace("T"," "))}</div>'
            '</div>'
        )
    calendar = '<div class="panel"><h2>Календарь / agenda</h2><div class="calendar">' + "".join(f'<div class="day">{x}</div>' for x in calendar_items) + '</div></div>'

    packages_html = []
    for key, group in sorted(groups.items(), key=lambda item: min(s.get("publish_at","") for s in item[1])):
        topic = group[0].get("topic") or key
        task_ids = {s.get("task_id") for s in group if s.get("task_id")}
        package_artifacts = []
        for task_id in task_ids:
            package_artifacts.extend(by_task.get(task_id, []))
        chips = "".join(f'<span class="chip">{esc(platform_label(s.get("platform","")))}</span>' for s in group)
        states = ", ".join(sorted({str(s.get("state") or "planned") for s in group}))
        queue_button = ""
        if plan_row and plan.get("approval_state") == "active" and not task_ids:
            queue_button = (
                f'<form method="post" action="/studio/products/{product.id}/plans/{plan_row.id}/packages/{esc(key)}/queue">'
                '<button class="btn secondary">Подготовить пакет сейчас</button></form>'
            )
        assets_markup = "".join(artifact_html(a) for a in package_artifacts if a.kind in {
            "article","channel_text","video_script","content_distribution",
            "dzen_brief","telegram_brief","short_video_brief"
        })
        if not assets_markup:
            assets_markup = '<p class="muted">Материалы ещё не созданы.</p>'
        packages_html.append(
            '<div class="card package">'
            f'<h3>{esc(topic)}</h3><div class="chips">{chips}</div>'
            f'<p class="muted">package_key: {esc(key)} · {esc(states)}</p>'
            f'{queue_button}<details><summary>Материалы</summary>{assets_markup}</details></div>'
        )
    packages = '<div class="panel"><h2>Контент-пакеты</h2><div class="packages">' + "".join(packages_html) + '</div></div>'

    # Library is intentionally independent of the current calendar: owner-created and older
    # content must remain discoverable even when no plan slot points at its task.
    content_tasks = [t for t in tasks if t.assigned_agent == "content_factory"]
    library_cards = []
    for task in content_tasks[:30]:
        task_assets = [
            a for a in by_task.get(str(task.id), [])
            if a.kind in {"article", "channel_text", "video_script", "content_distribution",
                          "dzen_brief", "telegram_brief", "short_video_brief",
                          "story_brief", "static_image_brief"}
        ]
        if not task_assets:
            continue
        library_cards.append(
            '<div class="card package">'
            f'<h3>{esc(task.title)}</h3>'
            f'<p class="muted">Статус: {esc(task.status.value)} · задача {esc(task.id)}</p>'
            f'<details><summary>Открыть все материалы ({len(task_assets)})</summary>'
            + "".join(artifact_html(a) for a in task_assets)
            + '</details></div>'
        )
    library = (
        '<div class="panel"><h2>Библиотека всех материалов</h2>'
        '<p class="muted">Здесь остаются и плановые, и вручную созданные пакеты — независимо от текущего календаря.</p>'
        '<div class="packages">' + ("".join(library_cards) or '<p class="muted">Готовых материалов пока нет.</p>') + '</div></div>'
    )

    plan_meta = ""
    if plan:
        plan_meta = (
            f'<p class="muted">План: {esc(plan.get("approval_state"))} · '
            f'период {esc(plan.get("horizon_days"))} дней · пересмотр {esc(plan.get("review_at"))}</p>'
            f'<div class="chips">{"".join(f"<span class=\"chip\">{esc(platform_label(c))}</span>" for c in channels)}</div>'
        )
    else:
        plan_meta = '<p class="muted">Контент-план пока не создан.</p>'

    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Content Studio · {esc(product.name)}</title><style>{CSS}</style></head>
<body><main class="shell">
<div class="top"><div><a href="/studio">← компании</a><h1>Content Studio · {esc(product.name)}</h1>{plan_meta}</div>
<div class="actions">{action_html}</div></div>
{stats}
<div style="height:16px"></div>{calendar}
<div style="height:16px"></div>{packages}
<div style="height:16px"></div>{library}
<div style="height:20px"></div><p class="muted">Публикация и видео-генерация здесь не выполняются. Production API должен оставаться за защищённой границей.</p>
</main></body></html>"""


@router.get("/studio", response_class=HTMLResponse)
def studio(db: Session = Depends(get_db)):
    products = list(db.scalars(select(Product).order_by(Product.name).limit(100)))
    cards = "".join(
        f'<a class="product-link card" href="/studio/products/{p.id}"><b>{esc(p.name)}</b><div class="muted">{esc(p.slug)}</div></a>'
        for p in products
    )
    response = HTMLResponse(
        f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>Content Studio</title><style>{CSS}</style></head><body><main class="shell"><h1>Content Studio</h1>'
        f'<p class="muted">Визуальная редакционная панель AI Growth OS.</p><div class="product-list">{cards}</div></main></body></html>'
    )
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
    slots = [s for s in (plan.get("slots") or []) if s.get("package_key") == package_key]
    if not slots:
        raise HTTPException(404, "Package not found")
    topic = slots[0].get("topic")
    audience = slots[0].get("audience")
    objective = slots[0].get("objective") or ""
    channels = ", ".join(sorted({s.get("platform","") for s in slots}))
    queue_content(
        db, product_id, topic,
        dedupe_key=f"calendar:{plan_id}:{package_key}",
        audience=audience,
        editorial_brief=f"Цель пакета: {objective}\nПлощадки: {channels}\nПлан подтверждает только подготовку; публикация вручную."
    )
    return RedirectResponse(f"/studio/products/{product_id}", status_code=303)
