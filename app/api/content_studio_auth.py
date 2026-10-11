"""Temporary owner-only protection for Growth Studio.

This is not a multi-tenant login system. Production refuses all Studio routes
unless operator credentials are explicitly configured. Other FastAPI routes
still require network/edge protection until full user RBAC is implemented.
"""
from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.config import Settings, get_settings

_basic = HTTPBasic(auto_error=False)


def require_studio_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
):
    username = settings.studio_admin_username
    password = settings.studio_admin_password
    production = settings.app_env.casefold() in {"production", "prod"}

    if not username or not password:
        if production or username or password:
            # Fail closed on misconfigured production AND partial configuration.
            raise HTTPException(503, "Growth Studio owner login is not configured")
        return None  # Developer loopback only; never a production exemption.

    if not credentials:
        raise HTTPException(401, "Authentication required",
                            headers={"WWW-Authenticate": "Basic"})
    valid_user = secrets.compare_digest(credentials.username.encode(), username.encode())
    valid_password = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not valid_user or not valid_password:
        raise HTTPException(401, "Incorrect credentials",
                            headers={"WWW-Authenticate": "Basic"})
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
        raise HTTPException(503, "Studio public origin is not configured")
    allowed = urlsplit(configured)
    if allowed.scheme != "https" or not allowed.netloc or allowed.username or allowed.password:
        raise HTTPException(503, "Studio public origin must be an HTTPS origin")
    if allowed.path not in ("", "/") or allowed.query or allowed.fragment:
        raise HTTPException(503, "Studio public origin must not include a path or query")
    if request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
        raise HTTPException(403, "Cross-site action rejected")

    submitted_origin = request.headers.get("origin")
    if not submitted_origin:
        # Legacy browser fallback; never guess the trusted origin from Host.
        submitted_origin = request.headers.get("referer")
    if not submitted_origin:
        raise HTTPException(403, "Origin verification required")
    received = urlsplit(submitted_origin)
    if (received.scheme.casefold() != allowed.scheme.casefold()
            or received.netloc.casefold() != allowed.netloc.casefold()
            or received.username or received.password):
        raise HTTPException(403, "Cross-origin action rejected")
    return None
