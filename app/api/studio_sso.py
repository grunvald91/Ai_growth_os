"""Landing page for the one-time link from the ServGo admin.

Public on purpose: it is where a signed ServGo link arrives. It trusts nothing in the URL except
a correctly signed, unexpired, never-used ticket, and ServGo confirms access once more before the
studio session starts.
"""
from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.studio_login import LOGO, STYLE
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.sso import access, tickets
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


@router.get("/studio/sso", response_class=HTMLResponse)
def sso_landing(request: Request, t: str | None = None, db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)):
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
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == claims.business_id))
    if link is None:
        name = f"{claims.name} (№{claims.business_id})" if claims.name else f"№{claims.business_id}"
        return _message("Заведение ещё не подключено", f"«{name}» пока не привязано к компании в студии. "
                        "Попросите владельца студии подключить его в разделе «Аккаунты».", 404)
    now = datetime.now(timezone.utc)
    try:
        db.execute(delete(StudioSsoTicket).where(StudioSsoTicket.expires_at < now - timedelta(hours=1)))
        db.add(StudioSsoTicket(jti=claims.jti, product_id=link.product_id, servgo_user_id=claims.user_id,
                               servgo_business_id=claims.business_id,
                               expires_at=datetime.fromtimestamp(claims.expires, timezone.utc)))
        db.commit()
    except IntegrityError:
        db.rollback()  # The same link a second time.
        return _message(*EXPIRED, action=("Войти по логину", "/studio/login"))
    claims = tickets.Claims(claims.user_id, claims.business_id, role, claims.name, claims.jti, claims.expires)
    response = RedirectResponse(f"/studio/products/{link.product_id}", status_code=303, headers=HEADERS)
    response.set_cookie(tickets.COOKIE, tickets.issue_session(settings, claims, str(link.product_id)),
                        max_age=tickets.MAX_AGE, path="/", httponly=True, samesite="lax", secure=_secure(settings))
    return response
