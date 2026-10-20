"""Signed login sessions for Growth Studio.

Browser Basic auth cannot switch users, cannot log out, rejects non-ASCII passwords
and shows an unfriendly dialog on phones. A login form sets a signed cookie instead.

The cookie carries the login, an expiry and a fingerprint of the current credential
(password hash). Changing or resetting a password, or deleting the account, changes
or removes the fingerprint, so old cookies stop working immediately. The signing key
comes from STUDIO_SESSION_SECRET or a private file next to the accounts file.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

COOKIE = "sg_session"
MAX_AGE = 30 * 24 * 3600
_FAILURES: dict[str, list[float]] = {}
FAILURE_WINDOW = 15 * 60
MAX_FAILURES = 8          # per login
MAX_IP_FAILURES = 30      # per address: an office shares one IP


def normalize_login(value: str) -> str:
    # Phones capitalize the first letter and keyboards add trailing spaces.
    return (value or "").strip().lower()


def fingerprint(secret_material: str) -> str:
    return hashlib.sha256(secret_material.encode()).hexdigest()[:24]


def _key_path(settings) -> Path:
    from app.core.studio_accounts import accounts_path
    path, _ = accounts_path(settings)
    return path.parent / "session.key"


def signing_key(settings) -> bytes:
    configured = getattr(settings, "studio_session_secret", None)
    if configured:
        return hashlib.sha256(configured.encode()).digest()
    path = _key_path(settings)
    try:
        return bytes.fromhex(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            # Another worker may have created it while we waited for the lock.
            return bytes.fromhex(path.read_text().strip())
        except (FileNotFoundError, ValueError):
            key = secrets.token_bytes(32)
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                stream.write(key.hex())
            return key
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(settings, login: str, credential_fingerprint: str, *, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": login, "f": credential_fingerprint,
                               "e": int((now or time.time()) + MAX_AGE)}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(signing_key(settings), payload.encode(), "sha256").digest())
    return f"{payload}.{signature}"


def read(settings, token: str | None, *, now: float | None = None) -> tuple[str, str] | None:
    """Return (login, credential fingerprint) for a valid, unexpired cookie."""
    if not token or token.count(".") != 1 or len(token) > 1000:
        return None
    payload, signature = token.split(".")
    expected = _b64(hmac.new(signing_key(settings), payload.encode(), "sha256").digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        data = json.loads(_unb64(payload))
        login, credential, expires = data["u"], data["f"], int(data["e"])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(login, str) or not isinstance(credential, str) or expires < (now or time.time()):
        return None
    return login, credential


def too_many_failures(key: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _FAILURES.get(key, []) if now - t < FAILURE_WINDOW]
    _FAILURES[key] = recent
    return len(recent) >= (MAX_IP_FAILURES if key.startswith("ip:") else MAX_FAILURES)


def record_failure(key: str) -> None:
    if len(_FAILURES) > 5000:
        _FAILURES.clear()
    _FAILURES.setdefault(key, []).append(time.monotonic())


def clear_failures(key: str) -> None:
    _FAILURES.pop(key, None)
