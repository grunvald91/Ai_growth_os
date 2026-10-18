"""Growth Studio accounts created from the Studio itself.

Accounts live in one private JSON file on the persistent products volume
(or STUDIO_USERS_FILE when configured). Passwords are PBKDF2 hashes; plain
passwords are never stored or logged. Roles:

- ``admin``: every company, may manage accounts (like the operator login);
- ``company``: only the listed companies.
"""
from __future__ import annotations

import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROUNDS = 600_000
USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{2,39}")
ROLES = ("admin", "company")
# Verified logins are cached briefly so every page asset does not cost a
# 600k-round PBKDF2. The cache key is an HMAC with a per-process secret.
_CACHE_SECONDS = 300
_cache_key = secrets.token_bytes(32)
_verified: dict[str, float] = {}


class AccountError(ValueError):
    """A request the owner can fix; the message is shown in the Studio."""


def accounts_path(settings) -> tuple[Path, bool]:
    """Return the file and whether it was configured explicitly."""
    if settings.studio_users_file:
        return Path(settings.studio_users_file), True
    return Path(settings.products_root) / "_studio" / "accounts.json", False


def load_accounts(settings) -> dict:
    path, explicit = accounts_path(settings)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if explicit:
            raise
        return {}
    if not isinstance(data, dict):
        raise ValueError("Invalid accounts")
    return data


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ROUNDS).hex()
    return f"pbkdf2_sha256${ROUNDS}${salt.hex()}${digest}"


def verify_password(username: str, password: str, encoded: str) -> bool:
    scheme, rounds, salt, expected = encoded.split("$")
    if scheme != "pbkdf2_sha256" or not 600000 <= int(rounds) <= 2000000:
        raise ValueError("Invalid password hash")
    key = hmac.new(_cache_key, f"{username}\0{password}\0{encoded}".encode(), "sha256").hexdigest()
    cached = _verified.get(key)
    if cached and time.monotonic() - cached < _CACHE_SECONDS:
        return True
    computed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    if not secrets.compare_digest(computed, expected):
        return False
    if len(_verified) > 500:
        _verified.clear()
    _verified[key] = time.monotonic()
    return True


def account_scope(account: dict) -> list[str] | None:
    """None means every company; otherwise normalized company ids."""
    if account.get("role") == "admin":
        return None
    allowed = account["product_ids"]
    if not isinstance(allowed, list) or not all(isinstance(v, str) for v in allowed):
        raise ValueError("Invalid company membership")
    return [str(UUID(value)) for value in allowed]


class _Locked:
    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.handle = open(self.path.with_suffix(".lock"), "a")
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *args):
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()


def _write(path: Path, data: dict) -> None:
    temporary = path.with_name(".accounts-" + secrets.token_hex(6))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _check_password(password: str) -> str:
    if len(password) < 10 or len(password) > 200:
        raise AccountError("Пароль: от 10 до 200 символов")
    return password


def create_account(settings, *, username: str, password: str | None, role: str, product_ids: list[str],
                   created_by: str | None, reserved: set[str] = frozenset()) -> str:
    """Create an account; returns the password (generated when not given)."""
    username = username.strip().lower()
    if not USERNAME.fullmatch(username):
        raise AccountError("Логин: 3–40 символов, латиница, цифры, точка, дефис или подчёркивание")
    if role not in ROLES:
        raise AccountError("Выберите роль")
    companies = sorted({str(UUID(value)) for value in product_ids})
    if role == "company" and not companies:
        raise AccountError("Выберите хотя бы одну компанию")
    password = _check_password(password) if password else secrets.token_urlsafe(12)
    path, _ = accounts_path(settings)
    with _Locked(path):
        accounts = load_accounts(settings) if path.exists() else {}
        if username in accounts or username in reserved:
            raise AccountError("Такой логин уже есть")
        accounts[username] = {
            "password_hash": hash_password(password), "role": role,
            "product_ids": companies if role == "company" else [],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "created_by": created_by or "operator",
        }
        _write(path, accounts)
    return password


def reset_password(settings, username: str) -> str:
    password = secrets.token_urlsafe(12)
    path, _ = accounts_path(settings)
    with _Locked(path):
        accounts = load_accounts(settings)
        if username not in accounts:
            raise AccountError("Аккаунт не найден")
        accounts[username]["password_hash"] = hash_password(password)
        _write(path, accounts)
    _verified.clear()
    return password


def delete_account(settings, username: str, *, actor: str | None) -> None:
    if username == actor:
        raise AccountError("Нельзя удалить аккаунт, под которым вы вошли")
    path, _ = accounts_path(settings)
    with _Locked(path):
        accounts = load_accounts(settings)
        if accounts.pop(username, None) is None:
            raise AccountError("Аккаунт не найден")
        _write(path, accounts)
    _verified.clear()
