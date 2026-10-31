"""Operator login and company-scoped customer accounts shared by Studio and API.

Customer passwords use PBKDF2 hashes in a private operator-managed account file.
Browsers sign in through the Studio login form (signed session cookie); HTTP Basic
stays for API clients and browsers that already remember it. Production always
fails closed without configured credentials. Browser writes also require the
explicitly configured HTTPS origin.
"""
from __future__ import annotations

import base64
import binascii
import secrets
import json
import logging
from urllib.parse import quote
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.core.studio_sessions import fingerprint, normalize_login

_logger = logging.getLogger(__name__)

class StudioOriginError(HTTPException):
    """A denied command; presentation must never turn it into an accepted write."""


def _origin_for_log(value: str | None) -> str:
    if not value:
        return "missing"
    if value == "null":
        return "null"
    try:
        parsed = urlsplit(value)
        if parsed.scheme and parsed.hostname:
            port = f":{parsed.port}" if parsed.port else ""
            return f"{parsed.scheme}://{parsed.hostname}{port}"[:256]
    except ValueError:
        pass
    return "invalid"


def _reject_origin(request: Request, status: int, detail: str, configured: str | None):
    error_id = uuid4().hex[:12]
    request.state.studio_origin_error_id = error_id
    _logger.warning("studio_origin_rejected %s", json.dumps({
        "error_id": error_id, "reason": detail, "path": request.url.path,
        "configured_origin": _origin_for_log(configured),
        "origin": _origin_for_log(request.headers.get("origin")),
        "referer_origin": _origin_for_log(request.headers.get("referer")),
        "sec_fetch_site": request.headers.get("sec-fetch-site", "")[:32],
    }, ensure_ascii=True))
    raise StudioOriginError(status, detail)


class _Utf8Basic(HTTPBasic):
    """Browsers send UTF-8 for non-ASCII passwords; FastAPI only accepts ASCII."""

    async def __call__(self, request: Request) -> HTTPBasicCredentials | None:
        header = request.headers.get("Authorization", "")
        scheme, _, param = header.partition(" ")
        if scheme.lower() != "basic" or not param:
            return None
        try:
            raw = base64.b64decode(param.strip(), validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(401, "Invalid authentication credentials",
                                headers={"WWW-Authenticate": "Basic"}) from None
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")  # Older browsers send Latin-1.
        username, separator, password = text.partition(":")
        if not separator:
            raise HTTPException(401, "Invalid authentication credentials", headers={"WWW-Authenticate": "Basic"})
        return HTTPBasicCredentials(username=username, password=password)


_basic = _Utf8Basic(auto_error=False)


def _production(settings) -> bool:
    return settings.app_env.casefold() in {"production", "prod"}


def _load_users(settings) -> dict:
    from app.core.studio_accounts import load_accounts
    try:
        return load_accounts(settings)
    except (OSError, ValueError):
        raise HTTPException(503, "Studio accounts are not configured correctly") from None


def _htpasswd(settings) -> dict[str, str]:
    try:
        hashes = dict(line.split(":", 1) for line in settings.studio_htpasswd_file.read_text().splitlines()
                      if line and not line.startswith("#"))
        if not hashes or any(not value.startswith("$apr1$") for value in hashes.values()):
            raise ValueError("Unsupported operator hash")
    except (OSError, ValueError):
        raise HTTPException(503, "Studio operator hashes are not configured correctly") from None
    return hashes


def _operator_name(names, login: str) -> str | None:
    """Exact operator login first, then case-insensitive (phones capitalize)."""
    if login in names:
        return login
    folded = normalize_login(login)
    return next((name for name in names if name.casefold() == folded), None)


def credential_fingerprint(settings, users: dict, login: str) -> tuple[str, list[str] | None] | None:
    """Current (fingerprint, scope) for a login, or None when it no longer exists."""
    from app.core.studio_accounts import account_scope
    if login in users:
        account = users[login]
        try:
            return fingerprint(account["password_hash"]), account_scope(account)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(503, "Studio account is not configured correctly") from None
    if settings.studio_htpasswd_file:
        hashes = _htpasswd(settings)
        return (fingerprint(hashes[login]), None) if login in hashes else None
    if settings.studio_admin_username and settings.studio_admin_password and login == settings.studio_admin_username:
        return fingerprint(settings.studio_admin_username + "\0" + settings.studio_admin_password), None
    return None


def check_credentials(settings, users: dict, login: str, password: str) -> tuple[str, list[str] | None] | None:
    """(canonical login, scope) for valid credentials; None when wrong.

    Raises 503 when the account configuration itself is broken.
    """
    from app.core.studio_accounts import account_scope, verify_password
    key = normalize_login(login)
    if key in users:
        account = users[key]
        try:
            valid = verify_password(key, password, account["password_hash"])
            scope = account_scope(account)
        except (KeyError, ValueError, TypeError):
            raise HTTPException(503, "Studio account is not configured correctly") from None
        return (key, scope) if valid else None
    if settings.studio_htpasswd_file:
        # Reuse the current Nginx operator hashes; never replace credentials.
        from passlib.hash import apr_md5_crypt
        hashes = _htpasswd(settings)
        name = _operator_name(hashes, login.strip())
        try:
            valid = bool(name) and apr_md5_crypt.verify(password, hashes[name])
        except ValueError:
            raise HTTPException(503, "Invalid operator password hash") from None
        return (name, None) if valid else None
    username, configured = settings.studio_admin_username, settings.studio_admin_password
    if username and configured:
        name = _operator_name({username}, login.strip())
        valid_password = secrets.compare_digest(password.encode(), configured.encode())
        return (username, None) if name and valid_password else None
    return None


def _auth_configured(settings, users: dict) -> bool:
    username, password = settings.studio_admin_username, settings.studio_admin_password
    if users or settings.studio_htpasswd_file or (username and password):
        return True
    if _production(settings) or username or password:
        # Fail closed on misconfigured production AND partial configuration.
        raise HTTPException(503, "Growth Studio owner login is not configured")
    return False


def _session_login(request: Request, settings, users: dict) -> tuple[str, list[str] | None] | None:
    from app.core.studio_sessions import COOKIE, read
    session = read(settings, request.cookies.get(COOKIE))
    if session is None:
        return None
    login, credential = session
    current = credential_fingerprint(settings, users, login)
    if current is None or not secrets.compare_digest(current[0], credential):
        return None  # Password changed or account deleted: the cookie is no longer valid.
    return login, current[1]


def _sso_login(request: Request, settings, db=None) -> tuple[str, list[str]] | None:
    """Sign-in that started from the ServGo admin. ServGo is asked on every request, so removing
    someone there (or switching the administrators' access off) closes the studio at once."""
    from app.sso import access, tickets
    session = tickets.read_session(settings, request.cookies.get(tickets.COOKIE))
    if session is None or not tickets.configured(settings):
        return None
    if db is None or not _sso_link_current(db, session):
        return None  # The studio owner disconnected or re-linked this business: the old session ends.
    role = access.servgo_role(settings, session.user_id, session.business_id)
    if role is None:
        return None
    request.state.studio_sso_role = role
    return f"servgo:{session.user_id}", [session.product_id]


def _sso_link_current(db, session) -> bool:
    from sqlalchemy import select
    from app.sso.models import StudioBusinessLink
    linked = db.scalar(select(StudioBusinessLink.product_id).where(
        StudioBusinessLink.servgo_business_id == session.business_id))
    return linked is not None and str(linked) == session.product_id


def _wants_login_page(request: Request) -> bool:
    return (request.method in {"GET", "HEAD"} and request.url.path.startswith("/studio")
            and "text/html" in request.headers.get("accept", ""))


def _login_required(request: Request):
    if _wants_login_page(request):
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(303, "Login required", headers={"Location": "/studio/login?next=" + quote(target, safe="")})
    raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})


def _apply_scope(request: Request, login: str, allowed: list[str] | None) -> str:
    request.state.studio_product_ids = allowed
    request.state.studio_account_role = "admin" if allowed is None else "company"
    request.state.studio_login = login
    if allowed is None:
        return login
    product_id = request.path_params.get("product_id")
    if product_id is not None and str(product_id) not in allowed:
        raise HTTPException(404, "Компания не найдена")
    if request.url.path.startswith(("/intelligence", "/competitive-intelligence", "/studio/accounts")):
        raise HTTPException(403, "Administrator account required")
    if request.url.path == "/control/status" or (request.url.path == "/products" and request.method != "GET"):
        raise HTTPException(403, "Administrator account required")
    return login


def require_studio_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
    db=Depends(get_db),
):
    users = _load_users(settings)
    if not _auth_configured(settings, users):
        request.state.studio_product_ids = None
        return None  # Developer loopback only; never a production exemption.
    # A signed login session wins over credentials the browser remembers for Basic auth,
    # so signing in with another account through the form really switches the user.
    # It also wins over a ServGo sign-in: a link someone else sent can never push the
    # studio owner out of their own account (the landing page drops a narrower session instead).
    session = _session_login(request, settings, users)
    if session is not None:
        return _apply_scope(request, *session)
    sso = _sso_login(request, settings, db)
    if sso is not None:
        return _apply_scope(request, *sso)
    if not credentials:
        _login_required(request)
    result = check_credentials(settings, users, credentials.username, credentials.password)
    if result is None:
        raise HTTPException(401, "Incorrect credentials", headers={"WWW-Authenticate": "Basic"})
    return _apply_scope(request, *result)


def require_studio_csrf(
    request: Request,
    settings: Settings = Depends(get_settings),
):
    """Deny cross-origin owner commands even if the browser has cached Basic auth."""
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    if settings.app_env.casefold() not in {"production", "prod"}:
        return None
    configured = settings.studio_public_origin
    if not configured:
        _reject_origin(request, 503, "Studio public origin is not configured", configured)
    allowed = urlsplit(configured)
    if allowed.scheme != "https" or not allowed.netloc or allowed.username or allowed.password:
        _reject_origin(request, 503, "Studio public origin must be an HTTPS origin", configured)
    if allowed.path not in ("", "/") or allowed.query or allowed.fragment:
        _reject_origin(request, 503, "Studio public origin must not include a path or query", configured)
    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        _reject_origin(request, 403, "Cross-site action rejected", configured)

    submitted_origin = request.headers.get("origin")
    if not submitted_origin:
        # Legacy browser fallback; never guess the trusted origin from Host.
        submitted_origin = request.headers.get("referer")
    if not submitted_origin:
        _reject_origin(request, 403, "Origin verification required", configured)
    try:
        received = urlsplit(submitted_origin)
    except ValueError:
        _reject_origin(request, 403, "Cross-origin action rejected", configured)
    if received.username or received.password:
        _reject_origin(request, 403, "Cross-origin action rejected", configured)

    is_legacy_origin = (
        received.scheme.casefold() == allowed.scheme.casefold()
        and received.netloc.casefold() == allowed.netloc.casefold()
    )
    # This exception is deliberately NOT a blanket allowlist for another site's
    # Origin header. It only applies to a genuine same-origin request received
    # through the canonical HTTPS servgo.ru virtual host (nginx preserves Host).
    # A POST to growth.servgo.ru with Origin: https://servgo.ru is still rejected.
    is_servgo_gateway_origin = (
        settings.studio_servgo_gateway_enabled
        and received.scheme.casefold() == "https"
        and received.netloc.casefold() == "servgo.ru"
        and request.url.scheme.casefold() == "https"
        and request.url.netloc.casefold() == "servgo.ru"
    )
    if not (is_legacy_origin or is_servgo_gateway_origin):
        _reject_origin(request, 403, "Cross-origin action rejected", configured)
    return None
