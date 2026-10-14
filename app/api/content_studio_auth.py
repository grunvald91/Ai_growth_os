"""Operator login and company-scoped customer accounts shared by Studio and API.

Customer passwords use PBKDF2 hashes in a private operator-managed account file.
Production always fails closed without configured credentials. Browser writes
also require the explicitly configured HTTPS origin.
"""
from __future__ import annotations

import secrets
import hashlib
import json
import logging
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings

_basic = HTTPBasic(auto_error=False)
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


def require_studio_auth(
    request: Request,
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
):
    username = settings.studio_admin_username
    password = settings.studio_admin_password
    production = settings.app_env.casefold() in {"production", "prod"}

    users = {}
    if settings.studio_users_file:
        try:
            users = json.loads(settings.studio_users_file.read_text(encoding="utf-8"))
            if not isinstance(users, dict):
                raise ValueError("Invalid accounts")
        except (OSError, ValueError):
            raise HTTPException(503, "Studio accounts are not configured correctly") from None
    if credentials and credentials.username in users:
        account = users[credentials.username]
        try:
            scheme, rounds, salt, expected = account["password_hash"].split("$")
            if scheme != "pbkdf2_sha256" or not 600000 <= int(rounds) <= 2000000:
                raise ValueError("Invalid password hash")
            computed = hashlib.pbkdf2_hmac("sha256", credentials.password.encode(), bytes.fromhex(salt), int(rounds)).hex()
            valid = secrets.compare_digest(computed, expected)
            allowed = account["product_ids"]
            if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed):
                raise ValueError("Invalid company membership")
            from uuid import UUID
            allowed = [str(UUID(value)) for value in allowed]
        except (KeyError, ValueError, TypeError):
            raise HTTPException(503, "Studio account is not configured correctly") from None
        if not valid:
            raise HTTPException(401, "Incorrect credentials", headers={"WWW-Authenticate": "Basic"})
        request.state.studio_product_ids = allowed
        product_id = request.path_params.get("product_id")
        if product_id is not None and str(product_id) not in allowed:
            raise HTTPException(404, "Компания не найдена")
        if request.url.path.startswith(("/intelligence", "/competitive-intelligence")):
            raise HTTPException(403, "Administrator account required")
        if request.url.path == "/control/status" or (request.url.path == "/products" and request.method != "GET"):
            raise HTTPException(403, "Administrator account required")
        return credentials.username
    if settings.studio_htpasswd_file:
        # Reuse the current Nginx operator hashes; never replace credentials.
        from passlib.hash import apr_md5_crypt
        try:
            hashes = dict(line.split(":", 1) for line in settings.studio_htpasswd_file.read_text().splitlines() if line and not line.startswith("#"))
            if not hashes or any(not value.startswith("$apr1$") for value in hashes.values()):
                raise ValueError("Unsupported operator hash")
        except (OSError, ValueError):
            raise HTTPException(503, "Studio operator hashes are not configured correctly") from None
        if not credentials:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})
        encoded = hashes.get(credentials.username)
        try:
            valid = bool(encoded) and apr_md5_crypt.verify(credentials.password, encoded)
        except ValueError:
            raise HTTPException(503, "Invalid operator password hash") from None
        if not valid:
            raise HTTPException(401, "Incorrect credentials", headers={"WWW-Authenticate": "Basic"})
        request.state.studio_product_ids = None
        return credentials.username
    if not username or not password:
        if users and credentials:
            raise HTTPException(401, "Incorrect credentials", headers={"WWW-Authenticate": "Basic"})
        if users:
            raise HTTPException(401, "Authentication required", headers={"WWW-Authenticate": "Basic"})
        if production or username or password:
            # Fail closed on misconfigured production AND partial configuration.
            raise HTTPException(503, "Growth Studio owner login is not configured")
        request.state.studio_product_ids = None
        return None  # Developer loopback only; never a production exemption.

    if not credentials:
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Basic"})
    valid_user = secrets.compare_digest(credentials.username.encode(), username.encode())
    valid_password = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not valid_user or not valid_password:
        raise HTTPException(401, "Incorrect credentials",
                            headers={"WWW-Authenticate": "Basic"})
    request.state.studio_product_ids = None  # Explicit operator account.
    return username


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
    if (received.scheme.casefold() != allowed.scheme.casefold()
            or received.netloc.casefold() != allowed.netloc.casefold()
            or received.username or received.password):
        _reject_origin(request, 403, "Cross-origin action rejected", configured)
    return None

