"""Owner-editable agent prompts.

Every instruction an agent receives is registered here with a readable Russian title,
a description and its built-in default text. The owner can override the text on the
/studio/tune page; overrides live in PRODUCTS_ROOT/.prompt-overrides.json (shared by the
API and worker containers, like the router config) with optimistic revisions and a short
history per prompt. Without an override the default is used, so behaviour is unchanged.

Placeholders such as {evidence_refs} carry runtime data and must stay in the text.
Response schemas and runtime data are appended by the calling code and are not editable.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

MAX_PROMPT_CHARS = 30_000
HISTORY_LIMIT = 20
_FILE = ".prompt-overrides.json"
_LOCK = ".prompt-overrides.lock"


class PromptStoreError(ValueError):
    pass


class PromptConflict(PromptStoreError):
    pass


@dataclass(frozen=True)
class PromptSpec:
    key: str
    group: str
    title: str
    description: str
    default: str
    placeholders: tuple[str, ...] = ()


REGISTRY: dict[str, PromptSpec] = {}


def register(key: str, *, group: str, title: str, description: str, default: str,
             placeholders: tuple[str, ...] = ()) -> str:
    """Register a prompt at import time and return its key."""
    spec = PromptSpec(key, group, title, description, default, placeholders)
    existing = REGISTRY.get(key)
    if existing is not None and existing != spec:
        raise PromptStoreError(f"Prompt {key} registered twice with different text")
    for name in placeholders:
        if "{" + name + "}" not in default:
            raise PromptStoreError(f"Default of {key} lacks placeholder {name}")
    REGISTRY[key] = spec
    return key


def _root() -> Path:
    from app.core.config import get_settings
    root = Path(get_settings().products_root)
    if root.is_symlink():
        raise PromptStoreError("Prompt settings directory cannot be a symlink")
    return root


_cache: dict[str, tuple[float, dict]] = {}


def _read(root: Path) -> dict:
    path = root / _FILE
    if path.is_symlink():
        raise PromptStoreError("Prompt overrides cannot be a symlink")
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return {"revision": 0, "prompts": {}, "history": {}}
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(4_000_001)
    if len(raw) > 4_000_000:
        raise PromptStoreError("Prompt overrides are oversized")
    try:
        data = json.loads(raw)
        if not isinstance(data.get("revision"), int) or not isinstance(data.get("prompts"), dict):
            raise ValueError("Invalid prompt overrides")
        data.setdefault("history", {})
    except (ValueError, TypeError, AttributeError) as exc:
        raise PromptStoreError("Persisted prompt overrides are invalid") from exc
    _cache[str(path)] = (mtime, data)
    return data


def read_state() -> dict:
    root = _root()
    return _read(root) if root.exists() else {"revision": 0, "prompts": {}, "history": {}}


def text(key: str, **values: str) -> str:
    """The prompt the agent should use now: the owner's override, else the default."""
    spec = REGISTRY[key]
    try:
        override = read_state()["prompts"].get(key)
    except (PromptStoreError, OSError):
        override = None  # A broken override file must never stop agents; defaults stay safe.
    result = override["text"] if isinstance(override, dict) and isinstance(override.get("text"), str) else spec.default
    for name in spec.placeholders:
        result = result.replace("{" + name + "}", values.get(name, ""))
    return result


def validate(key: str, value: str) -> str:
    spec = REGISTRY.get(key)
    if spec is None:
        raise PromptStoreError("Неизвестный промпт")
    value = value.replace("\r\n", "\n").strip()
    if not 20 <= len(value) <= MAX_PROMPT_CHARS:
        raise PromptStoreError(f"Текст должен быть от 20 до {MAX_PROMPT_CHARS} символов")
    missing = [name for name in spec.placeholders if "{" + name + "}" not in value]
    if missing:
        raise PromptStoreError("Нельзя удалять подстановки: " + ", ".join("{" + m + "}" for m in missing))
    return value


@contextmanager
def _lock(root: Path):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root / _LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save(key: str, value: str | None, *, expected_revision: int, actor: str) -> dict:
    """Set (value) or reset to default (None) one prompt. Returns the new state."""
    if key not in REGISTRY:
        raise PromptStoreError("Неизвестный промпт")
    clean = None if value is None else validate(key, value)
    root = _root()
    with _lock(root):
        state = json.loads(json.dumps(_read(root)))
        if state["revision"] != expected_revision:
            raise PromptConflict("Промпты изменились в другой вкладке. Обновите страницу.")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        previous = state["prompts"].get(key)
        history = state["history"].setdefault(key, [])
        history.insert(0, {"text": previous["text"] if previous else None, "at": now, "by": actor})
        del history[HISTORY_LIMIT:]
        if clean is None or clean == REGISTRY[key].default:
            state["prompts"].pop(key, None)
        else:
            state["prompts"][key] = {"text": clean, "updated_at": now, "by": actor}
        state["revision"] += 1
        payload = json.dumps(state, ensure_ascii=False, indent=1).encode()
        fd, temp = tempfile.mkstemp(dir=root, prefix=".prompt-overrides.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, root / _FILE)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        _cache.pop(str(root / _FILE), None)
        return state
