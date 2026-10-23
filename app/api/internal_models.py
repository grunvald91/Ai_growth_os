"""The model chosen on the team page for roles that run outside this application (the ServGo mail).

ServGo's mail runs in another process on another codebase, so it cannot read the shared router file.
It asks here instead, with the same shared key the two applications already use for each other.
The path sits under /studio so the existing nginx rule for growth.servgo.ru already forwards it.
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException

from app.core.config import Settings, get_settings
from app.llm.router_store import role_override

router = APIRouter()

# Only roles whose work happens in ServGo are shared; everything else stays internal.
SHARED_ROLES = frozenset({"mail_agent"})


@router.get("/studio/internal/model/{role}")
def model_for_role(role: str, x_studio_key: str = Header(default=""), settings: Settings = Depends(get_settings)):
    key = settings.servgo_internal_key or ""
    if len(key) < 32 or not secrets.compare_digest(x_studio_key.encode(), key.encode()):
        raise HTTPException(401, "Нет доступа")
    if role not in SHARED_ROLES:
        raise HTTPException(404, "Роль не найдена")
    return {"role": role, "models": role_override(settings, role)}
