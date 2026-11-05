"""Shared Telegram Login Widget and Servgo web session helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Callable, Iterable


AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "servgo_session")
SESSION_EXPIRE_DAYS = int(os.getenv("AUTH_SESSION_DAYS", "30"))
DEFAULT_MAX_AUTH_AGE_SECONDS = 24 * 60 * 60


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def auth_db_config() -> dict:
    return {
        "host": os.getenv("AUTH_DB_HOST", os.getenv("DB_HOST", "localhost")),
        "port": os.getenv("AUTH_DB_PORT", os.getenv("DB_PORT", "5432")),
        "dbname": os.getenv("AUTH_DB_NAME", os.getenv("DB_NAME", "servgo_auth")),
        "user": os.getenv("AUTH_DB_USER", os.getenv("DB_USER", "postgres")),
        "password": os.getenv("AUTH_DB_PASSWORD", os.getenv("DB_PASSWORD", "")),
    }


def _get_conn():
    import psycopg2

    conn = psycopg2.connect(**auth_db_config())
    conn.autocommit = True
    return conn


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS web_users (
    id           SERIAL PRIMARY KEY,
    telegram_id  BIGINT UNIQUE NOT NULL,
    first_name   TEXT NOT NULL DEFAULT '',
    last_name    TEXT NOT NULL DEFAULT '',
    username     TEXT NOT NULL DEFAULT '',
    photo_url    TEXT NOT NULL DEFAULT '',
    auth_date    TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash     TEXT PRIMARY KEY,
    token          TEXT,
    telegram_id    BIGINT NOT NULL REFERENCES web_users(telegram_id),
    anon_user_id   BIGINT,
    source_service TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at     TIMESTAMPTZ NOT NULL,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_web_sessions_telegram
    ON web_sessions(telegram_id);
"""

_MIGRATION_SQL = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'web_users'
          AND column_name = 'auth_date'
          AND data_type IN ('integer', 'bigint')
    ) THEN
        ALTER TABLE web_users
            ALTER COLUMN auth_date TYPE TIMESTAMPTZ
            USING to_timestamp(auth_date);
    END IF;
END $$;

ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS token_hash TEXT;
ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS token TEXT;
ALTER TABLE web_sessions ADD COLUMN IF NOT EXISTS source_service TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_web_sessions_token_hash
    ON web_sessions(token_hash)
    WHERE token_hash IS NOT NULL;
"""


def init_tables(conn_factory: Callable = _get_conn) -> None:
    conn = conn_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(_INIT_SQL)
            cur.execute(_MIGRATION_SQL)
    finally:
        conn.close()


def _data_check_string(data: dict) -> str:
    filtered = {
        key: str(value)
        for key, value in data.items()
        if key != "hash" and value is not None
    }
    return "\n".join(f"{key}={value}" for key, value in sorted(filtered.items()))


def verify_telegram_login(
    data: dict,
    bot_tokens: Iterable[str],
    *,
    now: float | None = None,
    max_age_seconds: int = DEFAULT_MAX_AUTH_AGE_SECONDS,
) -> bool:
    received_hash = str(data.get("hash", ""))
    if not received_hash:
        return False

    try:
        auth_date = int(data.get("auth_date", 0))
    except (TypeError, ValueError):
        return False

    current_time = time.time() if now is None else now
    if current_time - auth_date > max_age_seconds:
        return False
    if auth_date - current_time > 60:
        return False

    data_check_string = _data_check_string(data)
    for token in bot_tokens:
        if not token:
            continue
        secret_key = hashlib.sha256(token.encode()).digest()
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()
        if hmac.compare_digest(calculated_hash, received_hash):
            return True
    return False


def auth_dict_from_payload(payload: dict) -> dict:
    auth_dict = {
        "id": str(payload.get("id", "")),
        "first_name": payload.get("first_name", "") or "",
        "auth_date": str(payload.get("auth_date", "")),
        "hash": payload.get("hash", "") or "",
    }
    for key in ("last_name", "username", "photo_url"):
        value = payload.get(key)
        if value:
            auth_dict[key] = value
    return auth_dict


def user_from_auth_dict(data: dict) -> dict:
    first_name = data.get("first_name", "") or ""
    last_name = data.get("last_name", "") or ""
    return {
        "telegram_id": int(data["id"]),
        "id": int(data["id"]),
        "first_name": first_name,
        "last_name": last_name,
        "username": data.get("username", "") or "",
        "photo_url": data.get("photo_url", "") or "",
        "full_name": f"{first_name} {last_name}".strip()
        or data.get("username", "")
        or f"User {data['id']}",
    }


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_or_update_user(data: dict, conn_factory: Callable = _get_conn) -> dict:
    from psycopg2.extras import RealDictCursor

    auth_date = datetime.fromtimestamp(int(data["auth_date"]), tz=timezone.utc)
    conn = conn_factory()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                INSERT INTO web_users (telegram_id, first_name, last_name, username, photo_url, auth_date, last_login)
                VALUES (%(tid)s, %(fn)s, %(ln)s, %(un)s, %(ph)s, %(ad)s, NOW())
                ON CONFLICT (telegram_id) DO UPDATE SET
                    first_name = EXCLUDED.first_name,
                    last_name  = EXCLUDED.last_name,
                    username   = EXCLUDED.username,
                    photo_url  = EXCLUDED.photo_url,
                    auth_date  = EXCLUDED.auth_date,
                    last_login = NOW()
                RETURNING telegram_id, first_name, last_name, username, photo_url
                """,
                {
                    "tid": int(data["id"]),
                    "fn": data.get("first_name", "") or "",
                    "ln": data.get("last_name", "") or "",
                    "un": data.get("username", "") or "",
                    "ph": data.get("photo_url", "") or "",
                    "ad": auth_date,
                },
            )
            return dict(cur.fetchone())
    finally:
        conn.close()


def _table_columns(conn, table_name: str) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            """,
            (table_name,),
        )
        return {row[0] for row in cur.fetchall()}


def create_session(
    telegram_id: int,
    anon_user_id: int | None = None,
    *,
    source_service: str = "",
    conn_factory: Callable = _get_conn,
) -> str:
    token = secrets.token_hex(32)
    token_hash = hash_session_token(token)
    conn = conn_factory()
    try:
        columns = _table_columns(conn, "web_sessions")
        insert_columns = ["telegram_id", "anon_user_id", "expires_at"]
        values = [telegram_id, anon_user_id, SESSION_EXPIRE_DAYS]
        placeholders = ["%s", "%s", "NOW() + (%s * INTERVAL '1 day')"]

        if "token_hash" in columns:
            insert_columns.insert(0, "token_hash")
            values.insert(0, token_hash)
            placeholders.insert(0, "%s")
        if "token" in columns:
            insert_columns.insert(0, "token")
            values.insert(0, token_hash)
            placeholders.insert(0, "%s")
        if "source_service" in columns:
            insert_columns.append("source_service")
            values.append(source_service)
            placeholders.append("%s")

        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO web_sessions ({", ".join(insert_columns)})
                VALUES ({", ".join(placeholders)})
                """,
                tuple(values),
            )
        return token
    finally:
        conn.close()


def get_session(token: str | None, conn_factory: Callable = _get_conn) -> dict | None:
    if not token:
        return None
    from psycopg2.extras import RealDictCursor

    token_hash = hash_session_token(token)
    conn = conn_factory()
    try:
        columns = _table_columns(conn, "web_sessions")
        filters = []
        params = []
        if "token_hash" in columns:
            filters.append("s.token_hash = %s")
            params.append(token_hash)
        if "token" in columns:
            filters.append("s.token = %s")
            params.append(token)
            filters.append("s.token = %s")
            params.append(token_hash)
        if not filters:
            return None

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT s.telegram_id, u.first_name, u.last_name, u.username, u.photo_url
                FROM web_sessions s
                JOIN web_users u ON u.telegram_id = s.telegram_id
                WHERE ({' OR '.join(filters)})
                  AND s.is_active = TRUE
                  AND s.expires_at > NOW()
                """,
                tuple(params),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def expire_session(token: str | None, conn_factory: Callable = _get_conn) -> None:
    if not token:
        return
    token_hash = hash_session_token(token)
    conn = conn_factory()
    try:
        columns = _table_columns(conn, "web_sessions")
        filters = []
        params = []
        if "token_hash" in columns:
            filters.append("token_hash = %s")
            params.append(token_hash)
        if "token" in columns:
            filters.append("token = %s")
            params.append(token)
            filters.append("token = %s")
            params.append(token_hash)
        if not filters:
            return

        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE web_sessions SET is_active = FALSE WHERE {' OR '.join(filters)}",
                tuple(params),
            )
    finally:
        conn.close()


def auth_cookie_kwargs() -> dict:
    domain = os.getenv("AUTH_COOKIE_DOMAIN", ".servgo.ru")
    options = {
        "max_age": SESSION_EXPIRE_DAYS * 24 * 60 * 60,
        "httponly": True,
        "secure": _env_bool("AUTH_COOKIE_SECURE", False),
        "samesite": os.getenv("AUTH_COOKIE_SAMESITE", "Lax"),
        "path": "/",
    }
    if domain:
        options["domain"] = domain
    return options


def auth_cookie_name() -> str:
    return AUTH_COOKIE_NAME


def clear_cookie_kwargs() -> dict:
    domain = os.getenv("AUTH_COOKIE_DOMAIN", ".servgo.ru")
    options = {"path": "/"}
    if domain:
        options["domain"] = domain
    return options
