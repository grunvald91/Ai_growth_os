"""Temporary owner-only protection for Growth Studio.

This is not a multi-tenant login system. Production refuses all Studio routes
unless operator credentials are explicitly configured. Other FastAPI routes
still require network/edge protection until full user RBAC is implemented.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException
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
