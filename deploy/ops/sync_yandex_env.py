#!/usr/bin/env python3
"""Provision an owner-supplied Yandex API key through an authenticated SSH stdin.

Run only on the Growth production host. Never print or expose the secret, and
store the environment reference in deployment configuration (not the value).
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ENV = Path("/opt/ai_growth_os/.env")
CONFIG = Path("/opt/ai_growth_os/deploy/deploy.json")


def atomic_write(path: Path, contents: str, *, default_mode: int = 0o600) -> None:
    if path.is_symlink():
        raise RuntimeError("Refusing to replace a symlink")
    old_stat = path.stat() if path.exists() else None
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
            mode = stat.S_IMODE(old_stat.st_mode) if old_stat else default_mode
            # Enforce root-only access even if an old .env was too permissive.
            if path == ENV:
                mode &= 0o600
            os.fchmod(handle.fileno(), mode)
            if old_stat:
                os.fchown(handle.fileno(), old_stat.st_uid, old_stat.st_gid)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> None:
    key = sys.stdin.read()
    if not key or len(key) > 4096 or any(ch in key for ch in ("\r", "\n", "\0")):
        raise SystemExit("API_YANDEX is missing or malformed; no changes written")
    if not ENV.is_file() or not CONFIG.is_file():
        raise SystemExit("Growth runtime .env or deploy.json is not configured")
    old = ENV.read_text(encoding="utf-8")
    # The API credential is a single value, never interpreted as shell commands.
    values = [line for line in old.splitlines()
              if line.partition("=")[0].strip() != "API_YANDEX"]
    values.append("API_YANDEX=" + key)
    settings = json.loads(CONFIG.read_text(encoding="utf-8"))
    overlay = settings.get("overlay_env")
    if not isinstance(overlay, dict):
        raise SystemExit("Growth deploy.json has no overlay_env")
    # Existing server deployment script already propagates overlay_env to all
    # Growth containers, including worker/API, even before script upgrades.
    overlay["API_YANDEX"] = "${API_YANDEX:-}"
    atomic_write(ENV, "\n".join(values) + "\n")
    atomic_write(CONFIG, json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    print("YANDEX_RUNTIME_SECRET_SYNCED")


if __name__ == "__main__":
    main()
