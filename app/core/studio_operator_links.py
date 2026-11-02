"""Explicit, revocable binding between ServGo SSO and a legacy Growth operator.

No account is linked by matching display names, emails or other unverified data.
The user must prove control of BOTH a live ServGo session and an admin-level
Growth credential. The file stores neither passwords nor bearer session tokens.
"""
from __future__ import annotations

import hmac
import json
import logging
from pathlib import Path

from app.core.studio_accounts import _Locked, _write, accounts_path

log = logging.getLogger(__name__)


def _path(settings) -> Path:
    account_file, _ = accounts_path(settings)
    return account_file.parent / "servgo_operator_links.json"


def bind_operator(settings, user_id: int, login: str, credential_fingerprint: str) -> None:
    if not isinstance(user_id, int) or user_id <= 0 or not login or len(credential_fingerprint) != 24:
        raise ValueError("Invalid operator binding")
    path = _path(settings)
    with _Locked(path):
        try:
            links = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(links, dict):
                raise ValueError("Invalid operator links")
        except FileNotFoundError:
            links = {}
        links[str(user_id)] = {"login": login, "credential_fingerprint": credential_fingerprint}
        _write(path, links)


def linked_operator(settings, user_id: int, accounts: dict) -> str | None:
    """Return admin username only while that credential remains active.

    A password reset, deleted account, changed operator secret or invalid link
    revokes the elevated role automatically. Fail closed for bad files.
    """
    try:
        links = json.loads(_path(settings).read_text(encoding="utf-8"))
        entry = links.get(str(user_id)) if isinstance(links, dict) else None
        if not isinstance(entry, dict):
            return None
        name, previous = entry.get("login"), entry.get("credential_fingerprint")
        if not isinstance(name, str) or not isinstance(previous, str):
            return None
        from app.api.content_studio_auth import credential_fingerprint
        active = credential_fingerprint(settings, accounts, name)
        if active is not None and active[1] is None and hmac.compare_digest(active[0], previous):
            return name
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        log.warning("growth_operator_link_invalid")
    return None


def unbind_operator(settings, user_id: int) -> None:
    """Revoke only the signed-in ServGo user's binding."""
    path = _path(settings)
    with _Locked(path):
        try:
            links = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if not isinstance(links, dict):
            raise ValueError("Invalid operator links")
        links.pop(str(user_id), None)
        _write(path, links)
