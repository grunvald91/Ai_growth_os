"""ServGo platform-owner mail workspace; protected by the booking login."""
import asyncio
import http.client
import ipaddress
import socket
import ssl
from contextlib import contextmanager
from html.parser import HTMLParser
import email
import email.policy
import imaplib
import json
import os
import re
import secrets
import smtplib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import User, get_db
from ..deps import get_current_user

router = APIRouter(prefix="/owner-mail", tags=["owner-mail"])
COOKIE = "servgo_owner_mail"
ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}$")
HOST = "https://mail.servgo.ru"
CANONICAL_HOST = "https://servgo.ru"
MAIL_GATEWAY_FLAG = "SERVGO_MAIL_GATEWAY_ENABLED"


def mail_gateway_enabled() -> bool:
    return os.getenv(MAIL_GATEWAY_FLAG, "").casefold() in {"1", "true", "yes"}


def canonical_mail_request(request: Request) -> bool:
    """Only requests genuinely served at the HTTPS PWA origin can use its Mail session."""
    return (mail_gateway_enabled() and request.url.scheme == "https"
            and request.url.netloc.casefold() == "servgo.ru")


def verify_mail_write_origin(request: Request):
    """Preserve legacy POST safety and bind new same-origin calls to their actual host."""
    sent = request.headers.get("origin")
    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise HTTPException(403, "Недопустимое происхождение запроса")
    if not mail_gateway_enabled():
        if sent != HOST:
            raise HTTPException(403, "Недопустимое происхождение запроса")
        return
    host = request.url.netloc.casefold()
    legacy = (request.url.scheme == "https" and host == "mail.servgo.ru" and sent == HOST)
    canonical = canonical_mail_request(request) and sent == CANONICAL_HOST
    if not (legacy or canonical):
        raise HTTPException(403, "Недопустимое происхождение запроса")
SESSION_SECONDS = 30 * 24 * 3600
# Mail login reuses the AI Growth OS password: Growth checks it, this service never stores it.
GROWTH_AUTH_URL = os.getenv("SERVGO_MAIL_GROWTH_AUTH_URL", "http://127.0.0.1:8091/studio/assets/servgo.svg")
LOGIN_USERNAMES = {x.strip().casefold() for x in os.getenv("SERVGO_MAIL_LOGIN_USERNAMES", "artur").split(",") if x.strip()}
LOGIN_FAILURES: dict[str, list[float]] = {}


def secret():
    key = os.getenv("SERVGO_MAIL_SESSION_SECRET", "")
    if len(key) < 32:
        raise HTTPException(503, "Настройка почтового кабинета не завершена")
    return key


def owner(user):
    expected_user = os.getenv("SERVGO_MAIL_OWNER_USER_ID", "")
    expected_tg = os.getenv("SERVGO_MAIL_OWNER_TELEGRAM_ID", "")
    return bool((expected_user and str(user.id) == expected_user) or
                (expected_tg and str(user.telegram_id) == expected_tg))


def require_owner(user: User = Depends(get_current_user)):
    if not owner(user):
        raise HTTPException(403, "Только владелец платформы")
    return user


@contextmanager
def db_open():
    root = Path(os.getenv("SERVGO_MAIL_DATA_DIR", "/var/lib/servgo-owner-mail"))
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise HTTPException(503, "Небезопасные права каталога")
    db = sqlite3.connect(root / "mail.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=15000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS claims (token_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS prospects (
          id TEXT PRIMARY KEY, company TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
          website TEXT NOT NULL, notes TEXT NOT NULL,
          consent TEXT NOT NULL DEFAULT '', excluded INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS drafts (
          prospect_id TEXT PRIMARY KEY, subject TEXT NOT NULL, body TEXT NOT NULL,
          reviewed INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'draft'
        );
        CREATE TABLE IF NOT EXISTS deliveries (
          id TEXT PRIMARY KEY, prospect_id TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS direct_mail (
          id TEXT PRIMARY KEY, email TEXT NOT NULL, subject TEXT NOT NULL,
          body TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
        );
    """)
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def sign(user_id, kind, seconds, **extra):
    return jwt.encode(
        {"sub": str(user_id), "kind": kind,
         "exp": datetime.now(timezone.utc) + timedelta(seconds=seconds), **extra},
        secret(), algorithm="HS256")


def verify(token, kind):
    try:
        value = jwt.decode(token, secret(), algorithms=["HS256"])
        if value.get("kind") != kind:
            raise ValueError("Bad purpose")
        return value
    except (JWTError, ValueError, TypeError):
        raise HTTPException(401, "Повторите вход через ServGo") from None


async def mail_owner(request: Request, session: AsyncSession = Depends(get_db)):
    raw = request.cookies.get(COOKIE)
    if not raw:
        raise HTTPException(401, "Сначала войдите в ServGo")
    token = verify(raw, "mail-session")
    try:
        user_id = int(token["sub"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(401, "Неверная сессия") from None
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not owner(user):
        raise HTTPException(403, "Нет доступа")
    if request.method not in {"GET", "HEAD"}:
        verify_mail_write_origin(request)
    return user


@router.post("/entry")
def entry(user: User = Depends(require_owner)):
    destination = "pwa" if mail_gateway_enabled() else "legacy"
    ticket = sign(user.id, "mail-entry", 60, jti=uuid4().hex, destination=destination)
    if destination == "pwa":
        return {"url": CANONICAL_HOST + "/api/owner-mail/claim?ticket=" + ticket}
    return {"url": HOST + "/claim?ticket=" + ticket}


@router.get("/claim")
async def claim(ticket: str, request: Request, session: AsyncSession = Depends(get_db)):
    data = verify(ticket, "mail-entry")
    is_pwa = canonical_mail_request(request)
    # Tickets minted for a canonical origin must not be accepted at the legacy
    # mail host, and legacy tickets must not be accepted at the PWA origin.
    if request.url.netloc.casefold() == "servgo.ru" and not is_pwa:
        raise HTTPException(403, "Вход через единое приложение не настроен")
    expected_destination = "pwa" if is_pwa else "legacy"
    if data.get("destination", "legacy") != expected_destination:
        raise HTTPException(401, "Ссылка предназначена для другого адреса")
    try:
        user_id = int(data["sub"])
        jti = str(data["jti"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(401, "Неверная ссылка") from None
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user or not owner(user):
        raise HTTPException(403, "Нет доступа")
    with db_open() as db:
        try:
            db.execute("INSERT INTO claims VALUES (?)", (jti,))
        except sqlite3.IntegrityError:
            raise HTTPException(401, "Ссылка использована") from None
    response = RedirectResponse(
        "/api/owner-mail/workspace" if is_pwa else "/", status_code=303,
    )
    set_session(response, user.id, cookie_path="/api/owner-mail" if is_pwa else "/")
    response.headers.update({"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"})
    return response


def set_session(response, user_id, *, cookie_path="/"):
    # Lax, not Strict: links from Gmail or Telegram must not drop the session. Every write
    # still requires strict, host-bound Origin checks for writes. A canonical PWA
    # session is scoped to the Mail API path and cannot be sent to other services.
    response.set_cookie(COOKIE, sign(user_id, "mail-session", SESSION_SECONDS),
                        secure=True, httponly=True, samesite="lax",
                        path=cookie_path, max_age=SESSION_SECONDS)


def too_many_failures(client: str) -> bool:
    now = time.monotonic()
    for key in list(LOGIN_FAILURES):
        LOGIN_FAILURES[key] = [t for t in LOGIN_FAILURES[key] if now - t < 900]
        if not LOGIN_FAILURES[key]:
            del LOGIN_FAILURES[key]
    total = sum(len(v) for v in LOGIN_FAILURES.values())
    # Per-address limit stops guessing; the global cap is high so strangers cannot lock the owner out.
    return len(LOGIN_FAILURES.get(client, ())) >= 8 or total >= 200


def note_failure(client: str):
    LOGIN_FAILURES.setdefault(client, []).append(time.monotonic())


class LoginData(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=500)


@router.post("/api/login")
async def login(data: LoginData, request: Request, session: AsyncSession = Depends(get_db)):
    verify_mail_write_origin(request)
    client = request.headers.get("x-real-ip") or (request.client.host if request.client else "?")
    if too_many_failures(client):
        raise HTTPException(429, "Слишком много попыток. Подождите 15 минут")
    # Case-insensitive; Cyrillic "Артур" is accepted as the Growth login "artur".
    username = data.username.strip().casefold()
    username = {"артур": "artur"}.get(username, username)
    valid = False
    if username in LOGIN_USERNAMES:
        try:
            async with httpx.AsyncClient(timeout=10) as client_http:
                answer = await client_http.get(GROWTH_AUTH_URL, auth=(username, data.password))
        except httpx.HTTPError:
            raise HTTPException(503, "Проверка пароля временно недоступна") from None
        if answer.status_code == 200:
            valid = True
        elif answer.status_code not in (401, 403):
            raise HTTPException(503, "Проверка пароля временно недоступна")
    if not valid:
        note_failure(client)
        raise HTTPException(401, "Неверный логин или пароль")
    try:
        owner_id = int(os.getenv("SERVGO_MAIL_OWNER_USER_ID", ""))
    except ValueError:
        raise HTTPException(503, "Владелец почты не настроен") from None
    user = (await session.execute(select(User).where(User.id == owner_id))).scalar_one_or_none()
    if not user or not owner(user):
        raise HTTPException(503, "Владелец почты не настроен")
    LOGIN_FAILURES.pop(client, None)
    response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    set_session(response, user.id, cookie_path="/api/owner-mail" if canonical_mail_request(request) else "/")
    return response


@router.get("/api/logout")
def logout(request: Request):
    is_pwa = canonical_mail_request(request)
    response = RedirectResponse("/admin" if is_pwa else "/", status_code=303, headers={
        "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
    })
    response.delete_cookie(
        COOKIE, path="/api/owner-mail" if is_pwa else "/",
        secure=True, httponly=True, samesite="lax",
    )
    return response


LOGIN_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Почта ServGo</title>
<link rel="stylesheet" href="/api/servgo-air-tokens.css?v=1">
<link rel="stylesheet" href="/api/servgo-mail-air.css?v=1">
<script src="/api/servgo-air-theme.js?v=1"></script>
<script src="/api/servgo-mail-theme-picker.js?v=1" defer></script>
<style>body{margin:0;font-family:var(--ds-font,system-ui),sans-serif;background:var(--s-bg,#fff);color:var(--s-text,#1b2d42);display:flex;
min-height:100vh;align-items:center;justify-content:center;padding:16px;box-sizing:border-box}
main{background:var(--s-surface,#fff);border-radius:20px;padding:24px;width:100%;max-width:360px;box-sizing:border-box;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{font-size:20px;margin:0 0 16px}label{display:block;font-size:14px;margin:0 0 12px}
input{display:block;width:100%;box-sizing:border-box;margin-top:4px;padding:10px 12px;font-size:16px;border:1px solid var(--s-line,#e5ecf4);background:var(--s-surface-2,#f6f9fd);color:var(--s-text,#1b2d42);border-radius:10px}
button{width:100%;margin-top:4px;background:var(--s-accent-grad,linear-gradient(110deg,#3174d4,#578fef));color:var(--s-accent-ink,#fff);border:0;padding:12px;border-radius:999px;font-size:16px;font-weight:600}
#err{color:#c00;font-size:14px;min-height:20px;margin:8px 0 0}
</style></head><body><main><h1>Почта ServGo</h1>
<label class="servgo-mail-theme-picker" style="margin:0 0 16px">Тема <select id="servgo-mail-theme" aria-label="Тема оформления"><option value="system">Как в системе</option><option value="light">Светлая</option><option value="dark">Тёмная</option></select></label>
<form id="f"><label>Логин<input name="username" autocomplete="username" autocapitalize="none" required></label>
<label>Пароль<input name="password" type="password" autocomplete="current-password" required></label>
<button type="submit">Войти</button><p id="err" role="alert"></p></form>
<script>
document.getElementById('f').addEventListener('submit', async function (e) {
  e.preventDefault();
  var err = document.getElementById('err'), btn = this.querySelector('button');
  err.textContent = ''; btn.disabled = true;
  try {
    var r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({username: this.username.value, password: this.password.value})});
    if (r.ok) { location.replace('/'); return; }
    var d = await r.json().catch(function () { return {}; });
    err.textContent = (d && typeof d.detail === 'string') ? d.detail : 'Не удалось войти';
  } catch (x) { err.textContent = 'Нет связи с сервером'; }
  btn.disabled = false;
});
</script></main></body></html>"""


@router.get("/app")
async def app_page(request: Request, session: AsyncSession = Depends(get_db)):
    try:
        await mail_owner(request, session)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return HTMLResponse(LOGIN_PAGE, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                                 "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                 "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
    path = Path(__file__).resolve().parent / "mail_campaign_dashboard.html"
    return FileResponse(path, media_type="text/html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        ),
    })


class ProspectData(BaseModel):
    company: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=250)
    website: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2000)


@router.get("/api/status")
def status(_: User = Depends(mail_owner)):
    return {
        "owner": True, "inbox_ready": bool(os.getenv("SERVGO_MAIL_IMAP_PASSWORD")),
        "smtp_ready": bool(os.getenv("SERVGO_MAIL_FROM")),
        "ai_ready": bool(os.getenv("CLAUDEHUB_API_KEY") or os.getenv("XAI_API_KEY")),
    }


@router.get("/api/prospects")
def prospects(_: User = Depends(mail_owner)):
    with db_open() as db:
        return [dict(x) for x in db.execute("""
            SELECT p.*, d.subject, d.body, d.reviewed, d.state
            FROM prospects p LEFT JOIN drafts d ON d.prospect_id=p.id
            ORDER BY p.rowid DESC LIMIT 500
        """).fetchall()]


@router.post("/api/prospects")
def add_prospect(value: ProspectData, _: User = Depends(mail_owner)):
    address = value.email.strip().lower()
    if not ADDRESS.fullmatch(address):
        raise HTTPException(422, "Проверьте email")
    if value.website:
        from urllib.parse import urlsplit
        parsed = urlsplit(value.website)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username
                or parsed.password or parsed.port not in (None, 443)):
            raise HTTPException(422, "Нужна ссылка HTTPS")
    item_id = uuid4().hex
    with db_open() as db:
        try:
            db.execute("INSERT INTO prospects VALUES (?,?,?,?,?,?,0)",
                       (item_id, value.company.strip(), address,
                        value.website.strip(), value.notes.strip(), ""))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Адрес уже сохранён") from None
    return {"id": item_id}


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []
        self.mailto = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith("mailto:"):
                self.mailto.append(href[7:].split("?", 1)[0].strip())

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def research_public_site(url: str, max_bytes: int = 150000, with_contacts: bool = False) -> dict:
    """Pinned-DNS HTTPS only; no redirects or private addresses."""
    if not url:
        return {"source": "", "excerpt": "", "status": "Сайт не указан"}
    from urllib.parse import urlsplit
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443)
                or parsed.username or parsed.password):
            return {"source": url, "excerpt": "", "status": "Недопустимый адрес сайта"}
        addresses = {item[4][0] for item in socket.getaddrinfo(
            parsed.hostname, 443, type=socket.SOCK_STREAM
        )}
        if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
            return {"source": url, "excerpt": "", "status": "Сайт не является публичным"}
        ip = sorted(addresses)[0]

        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                sock = socket.create_connection((ip, 443), timeout=8)
                self.sock = ssl.create_default_context().wrap_socket(
                    sock, server_hostname=parsed.hostname
                )

        conn = PinnedHTTPS(parsed.hostname, 443, timeout=8)
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            conn.request("GET", path, headers={
                "Host": parsed.hostname, "Accept-Encoding": "identity",
                "User-Agent": "ServGo-Prospect-Research/1.0",
            })
            res = conn.getresponse()
            if res.status != 200 or "text/html" not in res.getheader("Content-Type", ""):
                return {"source": url, "excerpt": "", "status": "HTTP " + str(res.status)}
            raw = res.read(max_bytes + 1)
            if len(raw) > max_bytes:
                return {"source": url, "excerpt": "", "status": "Страница слишком велика"}
            parser = VisibleHTML()
            parser.feed(raw.decode("utf-8", errors="replace"))
            full = " ".join(" ".join(parser.parts).split())
            found = {"source": url, "excerpt": full[:5500], "status": "Прочитан публичный сайт"}
            if with_contacts:  # the company search looks for an address in the whole page, not the excerpt
                found.update(mailto=parser.mailto, full_text=full)
            return found
        finally:
            conn.close()
    except (OSError, ValueError, http.client.HTTPException):
        return {"source": url, "excerpt": "", "status": "Сайт недоступен"}


async def studio_get(path: str):
    """GET from growth.servgo.ru's internal API with the shared key; None when it cannot be asked."""
    key = os.getenv("STUDIO_INTERNAL_KEY", "")
    if len(key) < 32:
        return None
    base = os.getenv("GROWTH_INTERNAL_URL", "https://growth.servgo.ru").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            result = await client.get(base + path, headers={"X-Studio-Key": key})
        if result.status_code != 200:
            return None
        return result.json()
    except (httpx.HTTPError, ValueError):
        return None


_TEAM_MODELS: dict = {"at": 0.0, "models": []}
TEAM_MODELS_SECONDS = 60  # a change on the team page reaches the mail within a minute


async def team_models() -> list[str]:
    """Models picked for the mail agent on the growth.servgo.ru team page (main first), or [] if none."""
    import time
    if time.monotonic() - _TEAM_MODELS["at"] < TEAM_MODELS_SECONDS:
        return list(_TEAM_MODELS["models"])
    data = await studio_get("/studio/internal/model/mail_agent")
    models = data.get("models") if isinstance(data, dict) else None
    chain = [m for m in models if isinstance(m, str) and m] if isinstance(models, list) else []
    _TEAM_MODELS.update(at=time.monotonic(), models=chain)
    return list(chain)


async def hub(prompt):
    key = os.getenv("CLAUDEHUB_API_KEY") or os.getenv("XAI_API_KEY")
    if not key:
        raise HTTPException(503, "Ключ ClaudeHub не подключён")
    # The owner's choice on the team page comes first; the env model is the fallback.
    chain = await team_models() or [os.getenv("SERVGO_MAIL_AI_MODEL", "grok-4.7")]
    for position, model in enumerate(chain):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": "Отвечай только JSON без пояснений."},
                         {"role": "user", "content": prompt}],
            # Reasoning models (DeepSeek, Kimi) spend tokens on thinking before the JSON answer;
            # a small cap cut the JSON off. 32000 fits every model in the ClaudeHub catalog.
            "max_completion_tokens": int(os.getenv("SERVGO_MAIL_AI_MAX_TOKENS", "32000")),
        }
        try:
            async with httpx.AsyncClient(timeout=float(os.getenv("SERVGO_MAIL_AI_TIMEOUT", "300"))) as client:
                result = await client.post("https://api.claudehub.fun/v1/chat/completions",
                                           headers={"Authorization": "Bearer " + key}, json=body)
                result.raise_for_status()
            raw = result.json()["choices"][0]["message"]["content"].strip()
            if raw.startswith(chr(96) * 3):
                raw = raw.strip(chr(96) + "json\n ")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Not JSON object")
            return parsed
        except httpx.HTTPStatusError as err:
            # Only a busy or broken provider moves on to the next model; a rejected request would fail again.
            transient = err.response.status_code in {429, 500, 502, 503, 504}
            if not transient or position == len(chain) - 1:
                raise HTTPException(502, "Модель не вернула корректный результат") from None
        except httpx.HTTPError:
            if position == len(chain) - 1:
                raise HTTPException(502, "Модель не вернула корректный результат") from None
        except (ValueError, KeyError, IndexError, TypeError):
            if position == len(chain) - 1:
                raise HTTPException(502, "Модель не вернула корректный результат") from None
    raise HTTPException(502, "Модель не вернула корректный результат")


@router.post("/api/draft/{prospect_id}")
async def generate_draft(prospect_id: str, _: User = Depends(mail_owner)):
    with db_open() as db:
        p = db.execute("SELECT * FROM prospects WHERE id=?", (prospect_id,)).fetchone()
        current = db.execute("SELECT * FROM drafts WHERE prospect_id=?", (prospect_id,)).fetchone()
    if not p or p["excluded"] or (current and current["state"] != "draft"):
        raise HTTPException(409, "Компания недоступна для нового черновика")
    evidence = await asyncio.to_thread(research_public_site, p["website"])
    context = json.dumps({
        "company": p["company"], "website": p["website"],
        "website_evidence": evidence,
        "notes_from_owner_unverified": p["notes"],
        "offer": "ServGo: онлайн-запись через сайт и Telegram, уведомления, ИИ-маркетинг",
        "link": "https://servgo.ru",
    }, ensure_ascii=False)
    proposal = await hub(
        "Подготовь персональное B2B-предложение на русском языке для компании. "
        "Если сведений недостаточно, не придумывай ни отзывы, ни проблемы, ни цифры. "
        "Коротко, без давления, до 1400 символов; одна ясная ссылка. "
        "Персонализация основана на цитируемых фактах сайта, не на догадках. "
        'Только JSON {"subject":"...","body":"..."}.\n' + context
    )
    subject, body = proposal.get("subject"), proposal.get("body")
    if not isinstance(subject, str) or not isinstance(body, str) or not (
        3 <= len(subject) <= 180 and 30 <= len(body) <= 4000
    ):
        raise HTTPException(502, "Некорректный черновик")
    critic = await hub(
        'Проверь письмо на существенные недостоверные факты и выдуманные результаты. '
        'Только JSON {"critical":true/false,"reason":"..."}.\n'
        + context + "\n" + json.dumps(proposal, ensure_ascii=False)
    )
    critical = critic.get("critical") is True
    if critical:
        corrected = await hub(
            'Исправь только существенные ошибки ниже. Сохрани удачную структуру '
            'и не добавляй неподтверждённых фактов. '
            'Только JSON {"subject":"...","body":"..."}.\n'
            + context + "\nПредыдущий текст:\n" + json.dumps(proposal, ensure_ascii=False)
            + "\nЗамечание:\n" + str(critic.get("reason", ""))[:600]
        )
        new_subject, new_body = corrected.get("subject"), corrected.get("body")
        if isinstance(new_subject, str) and isinstance(new_body, str) and (
            3 <= len(new_subject) <= 180 and 30 <= len(new_body) <= 4000
        ):
            subject, body = new_subject, new_body
    with db_open() as db:
        db.execute("""INSERT INTO drafts VALUES (?,?,?,0,'draft')
            ON CONFLICT(prospect_id) DO UPDATE SET subject=excluded.subject,
            body=excluded.body,reviewed=0,state='draft'""",
            (prospect_id, subject, body))
    return {"subject": subject, "body": body,
            "critic_warning": str(critic.get("reason", ""))[:500] if critical else "",
            "facts_verified": False, "sent": False}


class Approval(BaseModel):
    subject: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=30, max_length=4000)


@router.post("/api/approve/{prospect_id}")
def approve_draft(prospect_id: str, data: Approval, _: User = Depends(mail_owner)):
    with db_open() as db:
        p = db.execute("SELECT * FROM prospects WHERE id=?", (prospect_id,)).fetchone()
        d = db.execute("SELECT * FROM drafts WHERE prospect_id=?", (prospect_id,)).fetchone()
        if not p or not d or d["state"] != "draft" or p["excluded"]:
            raise HTTPException(409, "Нет доступного черновика")
        db.execute("UPDATE drafts SET subject=?,body=?,reviewed=1 WHERE prospect_id=?",
                   (data.subject.strip(), data.body.strip(), prospect_id))
    return {"approved": True}


@router.post("/api/send/{prospect_id}")
def send_draft(prospect_id: str, _: User = Depends(mail_owner)):
    sender = os.getenv("SERVGO_MAIL_FROM", "")
    if not ADDRESS.fullmatch(sender):
        raise HTTPException(503, "Не настроен исходящий адрес")
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        p = db.execute("SELECT * FROM prospects WHERE id=?", (prospect_id,)).fetchone()
        d = db.execute("SELECT * FROM drafts WHERE prospect_id=?", (prospect_id,)).fetchone()
        if not p or not d or p["excluded"] or not d["reviewed"] or d["state"] != "draft":
            raise HTTPException(409, "Отправка не разрешена или уже выполнялась")
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        used = db.execute("SELECT count(*) FROM deliveries WHERE created_at >= ?", (since,)).fetchone()[0]
        if used >= int(os.getenv("SERVGO_MAIL_DAILY_LIMIT", "100")):
            raise HTTPException(429, "Суточный лимит писем достигнут")
        delivery_id = uuid4().hex
        db.execute("UPDATE drafts SET state='sending' WHERE prospect_id=?", (prospect_id,))
        db.execute("INSERT INTO deliveries VALUES (?,?,?,?)",
                   (delivery_id, prospect_id, "delivery_unknown", datetime.now(timezone.utc).isoformat()))
        recipient, subject, body = p["email"], d["subject"], d["body"]
    message = EmailMessage()
    message["From"] = formataddr(("ServGo", sender))
    message["To"], message["Subject"] = recipient, subject
    message["Message-ID"] = "<" + delivery_id + "@servgo.ru>"
    message.set_content(body)
    try:
        with smtplib.SMTP("127.0.0.1", 25, timeout=15) as smtp:
            refusal = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            if refusal:
                raise smtplib.SMTPRecipientsRefused(refusal)
    except (OSError, smtplib.SMTPException):
        raise HTTPException(503, "Статус отправки неизвестен, повторение заблокировано") from None
    with db_open() as db:
        db.execute("UPDATE deliveries SET status='smtp_accepted' WHERE id=?", (delivery_id,))
        db.execute("UPDATE drafts SET state='smtp_accepted' WHERE prospect_id=?", (prospect_id,))
    return {"status": "smtp_accepted", "delivered": False}


@router.post("/api/exclude/{prospect_id}")
def exclude(prospect_id: str, _: User = Depends(mail_owner)):
    with db_open() as db:
        db.execute("UPDATE prospects SET excluded=1 WHERE id=?", (prospect_id,))
    return {"excluded": True}


def _mime(value):
    try:
        return str(make_header(decode_header(str(value or ""))))[:300]
    except (UnicodeError, ValueError):
        return ""


def imap_headers():
    user = os.getenv("SERVGO_MAIL_IMAP_USER", "")
    password = os.getenv("SERVGO_MAIL_IMAP_PASSWORD", "")
    if not user or not password:
        raise ValueError("IMAP credentials not configured")
    with imaplib.IMAP4_SSL("mail.servgo.ru", 993, timeout=12) as imap:
        imap.login(user, password)
        ok, _ = imap.select("INBOX", readonly=True)
        if ok != "OK":
            raise ValueError("Mailbox unavailable")
        ok, result = imap.uid("search", None, "ALL")
        if ok != "OK":
            return []
        items = []
        for uid in result[0].split()[-50:][::-1]:
            ok, parts = imap.uid("fetch", uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if ok != "OK":
                continue
            content = next((item[1] for item in parts if isinstance(item, tuple)), None)
            if content:
                head = email.message_from_bytes(content, policy=email.policy.default)
                items.append({"uid": uid.decode(), "from": _mime(head.get("From")),
                              "subject": _mime(head.get("Subject")),
                              "date": str(head.get("Date") or "")[:100]})
        return items


@router.get("/api/inbox")
async def inbox(_: User = Depends(mail_owner)):
    try:
        return await asyncio.to_thread(imap_headers)
    except (ValueError, OSError, imaplib.IMAP4.error):
        raise HTTPException(503, "Входящие недоступны. Нужен работающий IMAP") from None




class DirectDraftInput(BaseModel):
    recipient: str = Field(min_length=5, max_length=250)
    company: str = Field(default="", max_length=150)
    website: str = Field(default="", max_length=500)
    notes: str = Field(default="", max_length=2500)


class DirectSendInput(BaseModel):
    recipient: str = Field(min_length=5, max_length=250)
    subject: str = Field(min_length=3, max_length=180)
    body: str = Field(min_length=1, max_length=20000)
    request_id: str = Field(min_length=32, max_length=32)
    in_reply_to: str = Field(default="", max_length=260)


def clean_recipient(value: str) -> str:
    address = value.strip().lower()
    if not ADDRESS.fullmatch(address):
        raise HTTPException(422, "Введите один корректный email")
    return address


def validate_subject(value: str) -> str:
    subject = value.strip()
    if len(subject) < 3 or any(ord(char) < 32 for char in subject):
        raise HTTPException(422, "Проверьте тему письма")
    return subject


@router.post("/api/direct-draft")
async def direct_draft(info: DirectDraftInput, _: User = Depends(mail_owner)):
    """AI draft for an arbitrary email address, never requiring a contact."""
    recipient = clean_recipient(info.recipient)
    website = info.website.strip()
    if website:
        from urllib.parse import urlsplit
        parsed = urlsplit(website)
        if parsed.scheme != "https" or not parsed.hostname or parsed.port not in (None, 443) or parsed.username or parsed.password:
            raise HTTPException(422, "Укажите обычный HTTPS-сайт")
    evidence = await asyncio.to_thread(research_public_site, website)
    context = json.dumps({
        "recipient": recipient, "company": info.company,
        "website_information": evidence,
        "owner_notes_unverified": info.notes,
        "offer": "ServGo: онлайн-запись через сайт и Telegram, уведомления, ИИ-маркетинг",
        "landing": "https://servgo.ru",
    }, ensure_ascii=False)
    output = await hub(
        "Напиши персональное деловое письмо, опираясь только на факты из данных. "
        "Если сайт не указан, не придумывай специализацию компании или результаты. "
        "Короткий текст с одним призывом к действию. Только JSON "
        '{"subject":"...","body":"..."}.\n' + context
    )
    subject, body = output.get("subject"), output.get("body")
    if not isinstance(subject, str) or not isinstance(body, str) or not (3 <= len(subject) <= 180 and 30 <= len(body) <= 4000):
        raise HTTPException(502, "Некорректный черновик")
    critique = await hub(
        'Проверь исключительно существенные ошибки и неподтверждённые факты. '
        'Только JSON {"critical":true/false,"reason":"..."}.\n'
        + context + "\nЧерновик:\n" + json.dumps(output, ensure_ascii=False)
    )
    warning = ""
    if critique.get("critical") is True:
        warning = str(critique.get("reason", ""))[:350]
        corrected = await hub(
            "Исправь критические фактические ошибки из замечания. "
            "Не добавляй новых фактов. Только JSON "
            '{"subject":"...","body":"..."}.\n'
            + context + "\nЧерновик:\n" + json.dumps(output, ensure_ascii=False)
            + "\nЗамечания:\n" + warning
        )
        maybe_subject, maybe_body = corrected.get("subject"), corrected.get("body")
        if isinstance(maybe_subject, str) and isinstance(maybe_body, str) and 3 <= len(maybe_subject) <= 180 and 30 <= len(maybe_body) <= 4000:
            subject, body = maybe_subject, maybe_body
    return {
        "recipient": recipient, "subject": validate_subject(subject),
        "body": body, "critic_warning": warning,
        "research_status": evidence["status"], "sent": False,
    }


@router.post("/api/direct-send")
def direct_send(info: DirectSendInput, _: User = Depends(mail_owner)):
    """One explicit owner click, no address-book lookup or consent questionnaire."""
    recipient = clean_recipient(info.recipient)
    subject = validate_subject(info.subject)
    if not re.fullmatch(r"[a-f0-9]{32}", info.request_id):
        raise HTTPException(422, "Неверный идентификатор отправки")
    if info.in_reply_to and not MESSAGE_ID.fullmatch(info.in_reply_to):
        raise HTTPException(422, "Неверная ссылка на исходное письмо")
    sender = os.getenv("SERVGO_MAIL_FROM", "").strip()
    if not ADDRESS.fullmatch(sender):
        raise HTTPException(503, "Исходящий адрес ServGo не настроен")
    now = datetime.now(timezone.utc)
    with db_open() as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT status FROM direct_mail WHERE id=?", (info.request_id,)).fetchone()
        if old:
            raise HTTPException(409, "Это письмо уже было обработано. Проверьте отправленные")
        count = db.execute("""
            SELECT
              (SELECT count(*) FROM direct_mail WHERE created_at>=?) +
              (SELECT count(*) FROM deliveries WHERE created_at>=?)
        """, ((now - timedelta(days=1)).isoformat(), (now - timedelta(days=1)).isoformat())).fetchone()[0]
        if count >= int(os.getenv("SERVGO_MAIL_DAILY_LIMIT", "100")):
            raise HTTPException(429, "Суточный лимит писем достигнут")
        db.execute("""INSERT INTO direct_mail (id,email,subject,body,status,created_at)
            VALUES (?,?,?,?,?,?)""",
            (info.request_id, recipient, subject, info.body, "delivery_unknown", now.isoformat()))
    message = EmailMessage()
    message["From"] = formataddr(("ServGo", sender))
    message["To"] = recipient
    message["Subject"] = subject
    message["Message-ID"] = "<" + info.request_id + "@servgo.ru>"
    if info.in_reply_to:
        # The recipient's mail client shows the reply in the same thread.
        message["In-Reply-To"] = info.in_reply_to
        message["References"] = info.in_reply_to
    message.set_content(info.body)
    try:
        with smtplib.SMTP("127.0.0.1", 25, timeout=15) as smtp:
            failed = smtp.send_message(message, from_addr=sender, to_addrs=[recipient])
            if failed:
                raise smtplib.SMTPRecipientsRefused(failed)
    except (OSError, smtplib.SMTPException):
        # Timeout could have happened after DATA. No automatic retry.
        raise HTTPException(503, "Не могу подтвердить отправку. Проверьте историю") from None
    with db_open() as db:
        db.execute("UPDATE direct_mail SET status='smtp_accepted' WHERE id=?", (info.request_id,))
    return {"status": "smtp_accepted", "delivery_confirmed": False}

@router.get("/api/outbox")
def outbox(_: User = Depends(mail_owner)):
    with db_open() as db:
        return [dict(x) for x in db.execute("""
            SELECT status,created_at,company,email,subject,body FROM (
              SELECT l.status,l.created_at,p.company,p.email,
                     COALESCE(r.subject,'') AS subject,COALESCE(r.body,'') AS body
              FROM deliveries l JOIN prospects p ON p.id=l.prospect_id
              LEFT JOIN drafts r ON r.prospect_id=l.prospect_id
              UNION ALL
              SELECT d.status,d.created_at,d.email AS company,d.email,d.subject,d.body
              FROM direct_mail d
            ) ORDER BY created_at DESC LIMIT 100
        """).fetchall()]


@router.get("/permission")
def permission(_: User = Depends(require_owner)):
    return {"allowed": True}


def imap_message(uid: str):
    if not re.fullmatch(r"[1-9][0-9]{0,18}", uid):
        raise ValueError("Invalid UID")
    user = os.getenv("SERVGO_MAIL_IMAP_USER", "")
    password = os.getenv("SERVGO_MAIL_IMAP_PASSWORD", "")
    if not user or not password:
        raise ValueError("IMAP not configured")
    with imaplib.IMAP4_SSL("mail.servgo.ru", 993, timeout=12) as imap:
        imap.login(user, password)
        ok, _ = imap.select("INBOX", readonly=True)
        if ok != "OK":
            raise ValueError("Cannot select INBOX")
        ok, data = imap.uid("fetch", uid.encode("ascii"), "(BODY.PEEK[])")
        if ok != "OK":
            raise ValueError("Message not found")
        raw = next((row[1] for row in data if isinstance(row, tuple)), None)
        if not raw or len(raw) > 400_000:
            raise ValueError("Message is missing or oversized")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        body = msg.get_body(preferencelist=("plain",))
        text = ""
        if body:
            value = body.get_content()
            if isinstance(value, str):
                text = value[:30_000]
        if not text and not msg.is_multipart():
            value = msg.get_content()
            if isinstance(value, str) and msg.get_content_type() == "text/plain":
                text = value[:30_000]
        return {
            "uid": uid, "from": _mime(msg.get("From")),
            "subject": _mime(msg.get("Subject")),
            "date": str(msg.get("Date") or "")[:100], "body": text,
            **reply_fields(msg),
        }


MESSAGE_ID = re.compile(r"<[^<>\s]{3,250}>")


def reply_fields(msg) -> dict:
    """Where a reply goes and which letter it answers; empty when unusable."""
    address = parseaddr(_mime(msg.get("Reply-To")) or _mime(msg.get("From")))[1].strip().lower()
    message_id = str(msg.get("Message-ID") or "").strip()
    return {
        "reply_to": address if ADDRESS.fullmatch(address) else "",
        "message_id": message_id if MESSAGE_ID.fullmatch(message_id) else "",
    }


@router.get("/api/message/{uid}")
async def read_message(uid: str, _: User = Depends(mail_owner)):
    try:
        return await asyncio.to_thread(imap_message, uid)
    except (ValueError, OSError, imaplib.IMAP4.error, KeyError):
        raise HTTPException(404, "Письмо недоступно") from None
