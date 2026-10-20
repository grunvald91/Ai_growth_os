"""Studio sign-in and sign-out pages.

The page is public on purpose (it is where unauthenticated browsers land), so it
is self-contained: inline styles and logo, no protected Studio assets.
"""
from __future__ import annotations

import base64
import html

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.api.content_studio import _studio_form
from app.api.content_studio_auth import (
    _auth_configured, _load_users, check_credentials, credential_fingerprint, require_studio_csrf,
)
from app.api.content_studio_brand import SERVGO_FAVICON_SVG
from app.core.config import Settings, get_settings
from app.core.studio_sessions import (
    COOKIE, MAX_AGE, clear_failures, issue, normalize_login, record_failure, too_many_failures,
)

router = APIRouter(dependencies=[Depends(require_studio_csrf)])
LOGO = "data:image/svg+xml;base64," + base64.b64encode(SERVGO_FAVICON_SVG.encode()).decode()

STYLE = """
:root{--blue:#2F5BEB;--blue-dark:#244BD6;--text:#16202E;--muted:#526176;--line:#D9E3EB;--bg:#F4F6FA}
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);
 font:16px/1.5 Manrope,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{min-height:100vh;min-height:100dvh;display:flex;flex-direction:column;justify-content:space-between;
 max-width:440px;margin:0 auto;padding:32px 16px calc(24px + env(safe-area-inset-bottom))}
.brand{display:flex;align-items:center;gap:12px;font-weight:800;font-size:24px;letter-spacing:-.02em}
.brand b{font-weight:800}.brand span{color:var(--blue)}
h1{font-size:26px;line-height:1.25;margin:28px 0 6px;letter-spacing:-.02em}
p.lead{color:var(--muted);margin:0 0 8px}
.alert{border-radius:12px;padding:12px 14px;margin:16px 0 0;font-size:15px}
.alert.error{background:#FFF1EC;color:#9A3412}.alert.ok{background:#ECF6EF;color:#1F5D36}
form{display:grid;gap:14px;margin-top:20px}
label{display:grid;gap:6px;font-size:14px;font-weight:650;color:#324B68}
input{width:100%;min-height:52px;padding:12px 14px;border:1px solid var(--line);border-radius:12px;
 font:inherit;color:var(--text);background:#fff}
input:focus{outline:3px solid #C9D6FF;border-color:var(--blue)}
button{width:100%;min-height:54px;border:0;border-radius:14px;background:var(--blue);color:#fff;
 font:inherit;font-weight:700;font-size:17px;cursor:pointer}
button:active{background:var(--blue-dark)}
.foot{color:var(--muted);font-size:13px;text-align:center;margin-top:20px}
@media(min-width:700px){main{justify-content:center}.actions{margin-top:8px}}
"""


def _safe_next(value: str | None) -> str:
    # Only local Studio pages; never an open redirect.
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value[:500]
    return "/studio"


def _page(*, next_url: str, login: str = "", error: str | None = None, notice: str | None = None,
          status: int = 200) -> HTMLResponse:
    message = ""
    if error:
        message = f'<div class="alert error" role="alert">{html.escape(error)}</div>'
    elif notice:
        message = f'<div class="alert ok" role="status">{html.escape(notice)}</div>'
    body = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#F4F6FA"><title>Вход · Growth Studio</title>
<link rel="icon" href="{LOGO}"><style>{STYLE}</style></head>
<body><main>
<section>
  <div class="brand"><img src="{LOGO}" width="40" height="40" alt=""><b>Serv<span>Go</span></b></div>
  <h1>Вход в Growth Studio</h1>
  <p class="lead">Логин и пароль выдаёт администратор на странице «Аккаунты».</p>
  {message}
</section>
<form method="post" action="/studio/login" class="actions">
  <input type="hidden" name="next" value="{html.escape(next_url, quote=True)}">
  <label>Логин<input name="username" required autocomplete="username" autocapitalize="none"
    autocorrect="off" spellcheck="false" maxlength="80" value="{html.escape(login, quote=True)}"></label>
  <label>Пароль<input name="password" type="password" required autocomplete="current-password" maxlength="200"></label>
  <button type="submit">Войти</button>
  <p class="foot">Логин можно вводить с любой буквы — заглавной или строчной.</p>
</form>
</main></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store",
                                                           "X-Frame-Options": "DENY"})


def _secure_cookie(settings: Settings) -> bool:
    return settings.app_env.casefold() in {"production", "prod"}


@router.get("/studio/login", response_class=HTMLResponse)
def login_form(next: str | None = None, logged_out: bool = False):
    notice = "Вы вышли. Можно войти под другим логином." if logged_out else None
    return _page(next_url=_safe_next(next), notice=notice)


@router.post("/studio/login", response_class=HTMLResponse)
async def login_submit(request: Request, settings: Settings = Depends(get_settings)):
    fields = await _studio_form(request)
    login, password = fields.get("username", ""), fields.get("password", "")
    next_url = _safe_next(fields.get("next"))
    users = _load_users(settings)
    if not _auth_configured(settings, users):
        return RedirectResponse(next_url, status_code=303)  # Developer loopback without logins.
    client = request.headers.get("x-real-ip") or (request.client.host if request.client else "")
    keys = ("login:" + normalize_login(login), "ip:" + client)
    if any(too_many_failures(key) for key in keys):
        return _page(next_url=next_url, login=login, status=429,
                     error="Слишком много неудачных попыток. Подождите 15 минут и попробуйте снова.")
    result = check_credentials(settings, users, login, password) if login and password else None
    if result is None:
        for key in keys:
            record_failure(key)
        return _page(next_url=next_url, login=login, status=401,
                     error="Неверный логин или пароль. Проверьте раскладку клавиатуры и Caps Lock.")
    name, _ = result
    clear_failures(keys[0])
    credential, _ = credential_fingerprint(settings, users, name)
    response = RedirectResponse(next_url, status_code=303)
    response.set_cookie(COOKIE, issue(settings, name, credential), max_age=MAX_AGE, path="/",
                        httponly=True, samesite="lax", secure=_secure_cookie(settings))
    return response


@router.get("/studio/logout")
def logout(settings: Settings = Depends(get_settings)):
    response = RedirectResponse("/studio/login?logged_out=1", status_code=303)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=_secure_cookie(settings))
    return response

