"""Landing page for the one-time link from the ServGo admin.

Public on purpose: it is where a signed ServGo link arrives. It trusts nothing in the URL except
a correctly signed, unexpired, never-used ticket, and ServGo confirms access once more before the
studio session starts.
"""
from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.studio_login import LOGO, STYLE
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.intake.contracts import IntakeError
from app.models.domain import Product
from app.sso import access, tickets
from app.sso.company import MIN_ABOUT, about_text
from app.sso.models import StudioBusinessLink, StudioSsoTicket

router = APIRouter()
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


def _secure(settings: Settings) -> bool:
    return settings.app_env.casefold() in {"production", "prod"}


def _message(title: str, text: str, status: int, *, action: tuple[str, str] | None = None) -> HTMLResponse:
    button = (f'<form method="get" action="{html.escape(action[1], quote=True)}"><button type="submit">'
              f'{html.escape(action[0])}</button></form>') if action else ""
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer"><meta name="theme-color" content="#F4F6FA">
<title>{html.escape(title)} · Growth Studio</title><link rel="icon" href="{LOGO}"><style>{STYLE}</style></head>
<body><main><section>
  <div class="brand"><img src="{LOGO}" width="40" height="40" alt=""><b>Serv<span>Go</span></b></div>
  <h1>{html.escape(title)}</h1><p class="lead">{html.escape(text)}</p>
</section>{button}</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers=HEADERS)


EXPIRED = ("Ссылка устарела", "Ссылка из ServGo действует одну минуту и срабатывает один раз. "
           "Вернитесь в админку ServGo и нажмите «Маркетинг» ещё раз.", 400)


def _password_session_covers(request: Request, settings: Settings, product_id) -> bool | None:
    """None: no password sign-in in this browser. True: it already opens this company (the owner)."""
    from app.api.content_studio_auth import _load_users, _session_login
    try:
        session = _session_login(request, settings, _load_users(settings))
    except Exception:  # noqa: BLE001 - a broken accounts file must not block the ServGo sign-in.
        return None
    if session is None:
        return None
    allowed = session[1]
    return allowed is None or str(product_id) in allowed


def _cookie_redirect(settings: Settings, claims: tickets.Claims, role: str, product_id, target: str,
                     request: Request | None = None) -> RedirectResponse:
    session = tickets.Claims(claims.user_id, claims.business_id, role, claims.name, claims.jti, claims.expires)
    response = RedirectResponse(target, status_code=303, headers=HEADERS)
    if request is not None and _password_session_covers(request, settings, product_id) is False:
        # A password sign-in for other companies would hide this one; the person chose ServGo now.
        from app.core.studio_sessions import COOKIE as PASSWORD_COOKIE
        response.delete_cookie(PASSWORD_COOKIE, path="/", httponly=True, samesite="lax", secure=_secure(settings))
    response.set_cookie(tickets.COOKIE, tickets.issue_session(settings, session, str(product_id)),
                        max_age=tickets.MAX_AGE, path="/", httponly=True, samesite="lax", secure=_secure(settings))
    return response


def _ticket_row(claims: tickets.Claims, product_id: UUID) -> StudioSsoTicket:
    return StudioSsoTicket(jti=claims.jti, product_id=product_id, servgo_user_id=claims.user_id,
                           servgo_business_id=claims.business_id,
                           expires_at=datetime.fromtimestamp(claims.expires, timezone.utc))


def _link_for(db: Session, business_id: int) -> StudioBusinessLink | None:
    return db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == business_id))


def _use_ticket(db: Session, claims: tickets.Claims, product_id: UUID) -> bool:
    """Record the link as used. False when it was already used (a second try)."""
    now = datetime.now(timezone.utc)
    try:
        db.execute(delete(StudioSsoTicket).where(StudioSsoTicket.expires_at < now - timedelta(hours=1)))
        db.add(_ticket_row(claims, product_id))
        db.commit()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _first_visit(request: Request, background: BackgroundTasks, db: Session, settings: Settings,
                 claims: tickets.Claims, role: str):
    """First time this business opens the studio: the owner is asked whether to create its company."""
    if role not in ("owner", "co_owner"):
        name = f"{claims.name} (№{claims.business_id})" if claims.name else f"№{claims.business_id}"
        return _message("Сначала откроет владелец", f"Для «{name}» в студии ещё нет компании. Её создаёт владелец "
                        "при первом входе из ServGo — попросите его открыть «Маркетинг» один раз.", 403)
    return _ask_to_create(settings, claims)


ADMIN_URL = "https://servgo.ru/admin"


def _ask_to_create(settings: Settings, claims: tickets.Claims) -> HTMLResponse:
    name = html.escape(claims.name or f"№{claims.business_id}")
    token = html.escape(tickets.issue_intent(settings, claims), quote=True)
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer"><meta name="theme-color" content="#F4F6FA">
<title>Создать компанию · Growth Studio</title><link rel="icon" href="{LOGO}"><style>{STYLE}</style></head>
<body><main><section>
  <div class="brand"><img src="{LOGO}" width="40" height="40" alt=""><b>Serv<span>Go</span></b></div>
  <h1>Создать компанию «{name}»?</h1>
  <p class="lead">В студии ещё нет компании для этого заведения. Я возьму название, адрес и описание из ServGo
  и подготовлю профиль: останется ответить на несколько вопросов. Ничего не публикуется и не запускается.</p>
</section>
<form method="post" action="/studio/sso/create"><input type="hidden" name="c" value="{token}">
  <button type="submit">Создать компанию</button></form>
<form method="get" action="{ADMIN_URL}"><button type="submit">Не сейчас</button></form>
</main></body></html>"""
    return HTMLResponse(body, headers=HEADERS)


@router.post("/studio/sso/create", response_class=HTMLResponse)
def sso_create(request: Request, background: BackgroundTasks, c: str = Form(""),
               db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """The owner said yes. ServGo is asked again, so the answer is only as good as the access right now."""
    try:
        claims = tickets.read_intent(settings, c)
    except tickets.SsoError:
        return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
    access.forget(claims.user_id, claims.business_id)
    role = access.servgo_role(settings, claims.user_id, claims.business_id)
    if role not in ("owner", "co_owner"):
        return _message("Нет доступа", "ServGo не подтвердил, что вы владелец этого заведения.", 403,
                        action=("Войти по логину", "/studio/login"))
    link = _link_for(db, claims.business_id)
    if link is not None:  # Another tab created it meanwhile: just go there.
        if not _use_ticket(db, claims, link.product_id):
            return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
        return _cookie_redirect(settings, claims, role, link.product_id, f"/studio/products/{link.product_id}", request)
    return _create_company(request, background, db, settings, claims, role)


def _create_company(request: Request, background: BackgroundTasks, db: Session, settings: Settings,
                    claims: tickets.Claims, role: str):
    """Create the company, fill it from ServGo, start the intake."""
    from app.api.content_studio_company import ACTOR, _service, _start_job
    data = access.servgo_business(settings, claims.user_id, claims.business_id)
    if data is None:
        return _message("Не удалось получить данные заведения", "ServGo не ответил. Подождите минуту и откройте "
                        "«Маркетинг» из админки ещё раз.", 502)
    about = about_text(data)
    if len(about) < MIN_ABOUT:
        about = (about + " Подробности уточним у владельца.").strip().ljust(MIN_ABOUT, ".")
    product = Product(name=" ".join(str(data["name"]).split())[:200] or f"Заведение {claims.business_id}",
                      slug="company-" + uuid4().hex[:12], description=about[:10000])
    try:
        db.add(product)
        db.flush()
        db.add(StudioBusinessLink(servgo_business_id=claims.business_id, product_id=product.id))
        db.add(_ticket_row(claims, product.id))
        db.commit()
    except IntegrityError:
        db.rollback()  # Another tab created the company first, or this link was already used.
        link = _link_for(db, claims.business_id)
        if link is None or not _use_ticket(db, claims, link.product_id):
            return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
        return _cookie_redirect(settings, claims, role, link.product_id, f"/studio/products/{link.product_id}", request)
    target = f"/studio/products/{product.id}/profile"
    try:
        service = _service(db)
        session = service.start(product.id, actor=ACTOR)
        session_id = UUID(session["session_id"])
        service.add_text(product.id, session_id, about, actor=ACTOR, reference="ServGo: данные заведения")
        # The profile draft is built in the background; the page shows progress and asks for what is missing.
        _start_job(background, product.id, session_id, build=True)
    except IntakeError:
        db.rollback()  # The company exists; the owner continues from the profile page.
    return _cookie_redirect(settings, claims, role, product.id, target, request)


@router.get("/studio/sso", response_class=HTMLResponse)
def sso_landing(request: Request, background: BackgroundTasks, t: str | None = None,
                db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    if not tickets.configured(settings):
        return _message("Вход из ServGo не настроен", "Откройте студию через обычный вход.", 503,
                        action=("Войти по логину", "/studio/login"))
    try:
        claims = tickets.verify_ticket(settings, t)
    except tickets.SsoError:
        return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
    # ServGo confirms access right now: the link alone is not enough.
    access.forget(claims.user_id, claims.business_id)
    role = access.servgo_role(settings, claims.user_id, claims.business_id)
    if role is None:
        return _message("Нет доступа", "ServGo не подтвердил доступ к маркетингу для этого заведения. "
                        "Владелец может открыть его в настройках заведения.", 403,
                        action=("Войти по логину", "/studio/login"))
    link = _link_for(db, claims.business_id)
    if link is None:
        return _first_visit(request, background, db, settings, claims, role)
    if not _use_ticket(db, claims, link.product_id):
        return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
    return _cookie_redirect(settings, claims, role, link.product_id, f"/studio/products/{link.product_id}", request)
