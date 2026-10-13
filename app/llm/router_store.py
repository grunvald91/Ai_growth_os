"""Durable, owner-controlled router policy shared between API and worker containers.

Only model IDs, policy and *operator-entered* tariffs are stored here. No API keys.
A per-file advisory lock and optimistic revision prevent lost writes. Atomic
replacement means workers observe either the previous or complete new policy.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.core.config import Settings
from app.llm.model_router import ModelRouter, RoutingError


class RouterStoreError(ValueError):
    pass


class RouterConflict(RouterStoreError):
    pass


class RouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: StrictBool = False
    profiles: list[dict] = Field(min_length=1, max_length=40)
    overrides: dict[str, list[str]] = Field(default_factory=dict)
    max_call_usd: str | None = None


class RouterState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(default=0, ge=0)
    config: RouterConfig


class RouterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    config: RouterConfig


def default_configuration(settings: Settings) -> RouterConfig:
    raw = settings.model_router_profiles or Settings.model_fields["model_router_profiles"].default
    # Parsing here is intentional: it fails rather than accepting a broken env catalog.
    catalog = json.loads(raw)
    overrides = json.loads(settings.model_router_overrides or "{}")
    config = RouterConfig(
        enabled=settings.model_router_enabled,
        profiles=catalog, overrides=overrides,
        max_call_usd=settings.model_router_max_call_usd or None,
    )
    validate_configuration(config)
    return config


def validate_configuration(config: RouterConfig) -> ModelRouter:
    router = ModelRouter(
        profiles_json=json.dumps(config.profiles),
        overrides_json=json.dumps(config.overrides),
        max_call_usd=config.max_call_usd,
        provider_supports_web_search=False,
    )
    return router


def _root(settings: Settings) -> Path:
    root = Path(settings.products_root)
    if root.is_symlink():
        raise RouterStoreError("Router settings directory cannot be a symlink")
    return root


@contextmanager
def _file_lock(root: Path):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink():
        raise RouterStoreError("Router settings directory cannot be a symlink")
    lock_path = root / ".model-router.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _read(root: Path) -> RouterState | None:
    path = root / ".model-router-config.json"
    if path.is_symlink():
        raise RouterStoreError("Router configuration cannot be a symlink")
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise RouterStoreError("Router configuration is oversized")
    try:
        data = json.loads(raw)
        state = RouterState.model_validate(data)
        validate_configuration(state.config)
        return state
    except (ValueError, TypeError) as exc:
        raise RouterStoreError("Persisted router configuration is invalid") from exc


def read_state(settings: Settings) -> RouterState:
    root = _root(settings)
    if root.exists():
        state = _read(root)
        if state is not None:
            return state
    return RouterState(revision=0, config=default_configuration(settings))


def effective_router_settings(settings: Settings) -> Settings:
    """Hot-reload shared config for fresh provider instances, no process restart."""
    root = _root(settings)
    state = _read(root) if root.exists() else None
    if state is None:
        return settings
    config = state.config
    return settings.model_copy(update={
        "model_router_enabled": config.enabled,
        "model_router_profiles": json.dumps(config.profiles),
        "model_router_overrides": json.dumps(config.overrides),
        "model_router_max_call_usd": config.max_call_usd,
    })


def save_state(settings: Settings, update: RouterUpdate) -> RouterState:
    """Owner API authenticates before entering this function."""
    validate_configuration(update.config)
    root = _root(settings)
    with _file_lock(root):
        current = _read(root)
        if current is None:
            current = RouterState(revision=0, config=default_configuration(settings))
        if update.expected_revision != current.revision:
            raise RouterConflict("Router configuration changed; reload before saving")
        new = RouterState(revision=current.revision + 1, config=update.config)
        data = new.model_dump_json(indent=2).encode("utf-8")
        if len(data) > 65_536:
            raise RouterStoreError("Router configuration exceeds size limit")
        fd, temporary = tempfile.mkstemp(prefix=".model-router-", dir=root)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            destination = root / ".model-router-config.json"
            if destination.is_symlink():
                raise RouterStoreError("Router configuration cannot be a symlink")
            os.replace(temporary, destination)
            directory_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return new
