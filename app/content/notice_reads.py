"""Notifications the company has marked as read ("Прочитать все").

Stored per company on the products volume: a capped list of notice ids.
Marking read only hides the notice; the material or task itself is unchanged.
"""
from __future__ import annotations

import fcntl
import json
import os
import secrets
from pathlib import Path
from uuid import UUID

LIMIT = 2000


def _path(settings, product_id: UUID) -> Path:
    return Path(settings.products_root) / "_studio" / "notice-reads" / f"{UUID(str(product_id))}.json"


def read_ids(settings, product_id: UUID) -> set[str]:
    try:
        data = json.loads(_path(settings, product_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(value) for value in data} if isinstance(data, list) else set()


def mark_read(settings, product_id: UUID, ids: list[str]) -> int:
    clean = []
    for value in ids[:200]:
        try:
            clean.append(str(UUID(str(value))))
        except ValueError:
            continue
    path = _path(settings, product_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            current = current if isinstance(current, list) else []
        except (OSError, ValueError):
            current = []
        merged = list(dict.fromkeys([*current, *clean]))[-LIMIT:]
        temporary = path.with_name(".reads-" + secrets.token_hex(6))
        temporary.write_text(json.dumps(merged), encoding="utf-8")
        os.replace(temporary, path)
    return len(clean)
