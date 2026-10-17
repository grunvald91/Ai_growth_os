"""Add a company from Growth Studio and confirm its profile.

Reuses the company intake service (the same flow as the Telegram «Новая компания» button):
the owner's text and site become intake sources, the intake model drafts a profile, and
nothing reaches the canonical profile until the owner confirms it.
"""
from __future__ import annotations

from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.api.content_studio import (
    _owner_product, _studio_form, brand_html, esc, studio_page_shell,
)
from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.core.config import get_settings
from app.db.session import get_db
from app.intake.contracts import IntakeError
from app.intake.service import CompanyIntakeService
from app.intake.telegram import render_review
from app.models.domain import Product

router = APIRouter(dependencies=[Depends(require_studio_auth), Depends(require_studio_csrf)])
ACTOR = "local_owner"  # Same actor as the JSON intake API, so sessions are shared.


def _require_operator(request: Request) -> None:
    # Company-scoped accounts cannot create companies.
    if getattr(request.state, "studio_product_ids", None) is not None:
        raise HTTPException(403, "Добавлять компании может только владелец")


def _service(db: Session) -> CompanyIntakeService:
    settings = get_settings()
    return CompanyIntakeService(db, products_root=settings.products_root, model=settings.llm_model,
                                provider_name=settings.llm_provider)


def _new_company_page(fields: dict | None = None, error: str | None = None) -> str:
    fields = fields or {}
    notice = f'<div class="sg-warning" role="alert">{esc(error)}</div>' if error else ""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Новая компания · Growth Studio</title><link rel="icon" type="image/svg+xml" href="/studio/assets/servgo.svg">
<link rel="stylesheet" href="/studio/assets/studio.css?v=11"></head>
<body><main class="product-home">
<section class="panel hero">{brand_html()}<h1>Новая компания</h1>
<p class="subtitle">Опишите бизнес своими словами. Агенты соберут профиль компании, а вы проверите его перед работой.</p></section>
{notice}
<form class="panel sg-company-form" method="post" action="/studio/new" style="display:grid;gap:14px;padding:22px">
  <label>Название компании<input name="name" required minlength="2" maxlength="200" value="{esc(fields.get('name', ''))}"></label>
  <label>Сайт (если есть)<input name="website" type="url" maxlength="500" placeholder="https://" value="{esc(fields.get('website', ''))}"></label>
  <label>О компании
    <textarea name="about" required minlength="30" maxlength="12000" rows="10"
      placeholder="Чем занимается, какие услуги и цены, для кого, город, чем отличается, чего нельзя обещать.">{esc(fields.get('about', ''))}</textarea></label>
  <button class="btn primary" type="submit">Добавить компанию</button>
  <a href="/studio?choose=1">← К списку проектов</a>
</form></main></body></html>"""


@router.get("/studio/new", response_class=HTMLResponse)
def new_company_form(request: Request):
    _require_operator(request)
    return HTMLResponse(_new_company_page(), headers={"Cache-Control": "no-store"})


@router.post("/studio/new")
async def new_company_submit(request: Request, db: Session = Depends(get_db)):
    _require_operator(request)
    fields = await _studio_form(request)
    name = " ".join(fields.get("name", "").split())
    about = fields.get("about", "").strip()
    website = fields.get("website", "").strip()
    if not 2 <= len(name) <= 200 or not 30 <= len(about) <= 12000 or len(website) > 500:
        return HTMLResponse(_new_company_page(fields, "Укажите название (2–200 символов) и описание компании (от 30 символов)."),
                            status_code=422, headers={"Cache-Control": "no-store"})
    product = Product(name=name, slug="company-" + uuid4().hex[:12], description=about[:10000])
    db.add(product)
    db.commit()
    service = _service(db)
    try:
        session = service.start(product.id, actor=ACTOR)
        session_id = UUID(session["session_id"])
        service.add_text(product.id, session_id, about, actor=ACTOR, reference="growth studio: описание владельца")
        if website:
            try:
                await service.add_link(product.id, session_id, website, actor=ACTOR)
            except IntakeError:
                pass  # The company exists; the owner can add the site again from the profile page.
    except IntakeError as exc:
        db.rollback()
        return RedirectResponse(f"/studio/products/{product.id}/profile?error={quote(str(exc)[:200])}", status_code=303)
    return RedirectResponse(f"/studio/products/{product.id}/profile", status_code=303)


def _profile_body(product: Product, state: dict | None, error: str | None) -> str:
    base = f"/studio/products/{product.id}"
    parts = []
    if error:
        parts.append(f'<div class="sg-warning" role="alert">{esc(error)}</div>')
    if state is None:
        parts.append(f"""<section class="sg-surface"><h2>Сбор данных о компании</h2>
<p>Чтобы агенты писали точно про этот бизнес, соберите и подтвердите его профиль.</p>
<form method="post" action="{base}/profile/start"><button class="btn primary">Начать сбор данных</button></form></section>""")
        return "".join(parts)
    sources = "".join(
        f"<li>{esc(s.get('filename') or s.get('reference') or s.get('source_type'))}"
        f"{' — ' + esc(s.get('note')) if s.get('note') else ''}</li>"
        for s in state["sources"])
    parts.append(f"""<section class="sg-surface"><h2>Материалы</h2><ul>{sources or '<li>Пока ничего не добавлено.</li>'}</ul>
<form method="post" action="{base}/profile/source" style="display:grid;gap:10px">
  <label>Добавить сведения своими словами<textarea name="text" rows="4" maxlength="12000"></textarea></label>
  <label>или ссылку на сайт / страницу<input name="link" type="url" maxlength="500" placeholder="https://"></label>
  <button class="btn">Добавить</button></form></section>""")
    review = state.get("review")
    if review and state.get("review_current"):
        parts.append(f"""<section class="sg-surface"><h2>Черновик профиля</h2>
<div style="white-space:pre-wrap;line-height:1.5">{esc(render_review(review))}</div>
<form method="post" action="{base}/profile/approve"><input type="hidden" name="review_id" value="{esc(review['review_id'])}">
<button class="btn primary">Подтвердить профиль</button></form></section>""")
    else:
        parts.append(f"""<section class="sg-surface"><h2>Черновик профиля</h2>
<p>Агент изучит материалы и подготовит профиль. Это занимает 1–3 минуты.</p>
<form method="post" action="{base}/profile/build"><button class="btn primary">Собрать профиль</button></form></section>""")
    return "".join(parts)


def _render_profile(product: Product, db: Session, error: str | None = None, status_code: int = 200):
    service = _service(db)
    active = service.active(product.id, ACTOR)
    state = service.get(product.id, active.id, actor=ACTOR) if active else None
    page = studio_page_shell(product, active="profile", title="Профиль компании",
                             subtitle="Подтверждённые сведения, на которые опираются все агенты.",
                             body=_profile_body(product, state, error))
    return HTMLResponse(page, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/studio/products/{product_id}/profile", response_class=HTMLResponse)
def company_profile(product_id: UUID, error: str | None = None, db: Session = Depends(get_db)):
    return _render_profile(_owner_product(db, product_id), db, error[:300] if error else None)


def _session_id(service: CompanyIntakeService, product: Product) -> UUID:
    active = service.active(product.id, ACTOR)
    if active is None:
        raise IntakeError("Сначала начните сбор данных.")
    return active.id


@router.post("/studio/products/{product_id}/profile/{action}")
async def company_profile_action(product_id: UUID, action: str, request: Request, db: Session = Depends(get_db)):
    product = _owner_product(db, product_id)
    fields = await _studio_form(request)
    service = _service(db)
    try:
        if action == "start":
            service.start(product.id, actor=ACTOR)
        elif action == "source":
            session_id = _session_id(service, product)
            if fields.get("text", "").strip():
                service.add_text(product.id, session_id, fields["text"].strip(), actor=ACTOR,
                                 reference="growth studio: сведения владельца")
            if fields.get("link", "").strip():
                await service.add_link(product.id, session_id, fields["link"].strip(), actor=ACTOR)
        elif action == "build":
            from app.core.composition import build_intake
            session_id = _session_id(service, product)
            await build_intake(db, get_settings()).build(product.id, session_id, actor=ACTOR)
        elif action == "approve":
            session_id = _session_id(service, product)
            service.approve(product.id, session_id, UUID(fields.get("review_id", "")), actor=ACTOR, confirmed=True)
        else:
            raise HTTPException(404, "Неизвестное действие")
    except (IntakeError, LookupError) as exc:
        db.rollback()
        message = str(exc) if isinstance(exc, IntakeError) else "Сбор данных не найден."
        return _render_profile(product, db, message, status_code=409)
    except ValueError:
        db.rollback()
        return _render_profile(product, db, "Не удалось подготовить профиль. Материалы сохранены.", status_code=503)
    return RedirectResponse(f"/studio/products/{product.id}/profile", status_code=303)
