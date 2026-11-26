#!/usr/bin/env python3
"""Local, owner-operated Mail Exa configuration; no remote transport.

Run on the existing BookingBot host with the credential supplied by an
approved local secret provider. Missing credential is a no-op. Never pass
a credential on a command line or print it. Requires root for --apply.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICE = "booking-web-api.service"
SECRET = Path("/etc/servgo/mail-exa.env")
DROPIN = Path("/etc/systemd/system") / (SERVICE + ".d") / "90-servgo-mail-exa.conf"
HEALTH = "http://127.0.0.1:8000/health"


def valid_key(key: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{12,512}", key))


def system(*args: str) -> str:
    return subprocess.check_output(
        ["systemctl", *args], text=True, timeout=60,
        stderr=subprocess.DEVNULL,
    ).strip()


def healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=3) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def validate_exa(key: str) -> None:
    request = urllib.request.Request(
        "https://api.exa.ai/search",
        data=json.dumps({"query": "Exa search API documentation",
                         "type": "auto", "numResults": 1}).encode(),
        headers={"x-api-key": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            if not isinstance(json.load(response).get("results"), list):
                raise ValueError()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("EXA_HTTP_" + str(exc.code)) from None
    except Exception:
        raise RuntimeError("EXA_VALIDATION_FAILED") from None


def runtime_matches(key: str) -> bool:
    try:
        pid = int(system("show", SERVICE, "-p", "MainPID", "--value"))
        if pid <= 0:
            return False
        entries = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        return ("EXA_API_KEY=" + key).encode() in entries
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def secure_directory(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError("UNSAFE_DIRECTORY")
    path.mkdir(mode=0o755, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("UNSAFE_DIRECTORY")


def snapshot(path: Path) -> tuple[bytes, int] | None:
    if path.is_symlink():
        raise RuntimeError("UNSAFE_FILE")
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
        raise RuntimeError("UNSAFE_FILE")
    return path.read_bytes(), stat.S_IMODE(info.st_mode)


def atomic_write(path: Path, data: bytes, mode: int) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".mail-exa-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def restore(path: Path, old: tuple[bytes, int] | None) -> None:
    if old is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write(path, *old)


def wait_for(predicate, attempts: int = 30) -> bool:
    for _ in range(attempts):
        if predicate():
            return True
        time.sleep(2)
    return False


def provision(key: str, apply: bool = False) -> str:
    if not key:
        return "EXA_NOT_CONFIGURED_NO_CHANGES"
    if not valid_key(key):
        raise RuntimeError("EXA_KEY_FORMAT_INVALID")
    validate_exa(key)
    if not apply:
        return "EXA_VALIDATED_NO_CHANGES"
    if os.geteuid() != 0:
        raise RuntimeError("ROOT_REQUIRED")
    with open("/run/lock/servgo-mail-exa.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if system("is-active", SERVICE) != "active" or not healthy():
            raise RuntimeError("MAIL_API_UNHEALTHY_NO_CHANGES")
        secure_directory(SECRET.parent)
        secure_directory(DROPIN.parent)
        old_secret, old_drop = snapshot(SECRET), snapshot(DROPIN)
        desired_secret = ("EXA_API_KEY=" + key + "\n").encode()
        desired_drop = ("[Service]\nEnvironmentFile=" + str(SECRET) + "\n").encode()
        if (old_secret == (desired_secret, 0o600)
                and old_drop == (desired_drop, 0o644) and runtime_matches(key)):
            return "EXA_ALREADY_ACTIVE"
        try:
            atomic_write(SECRET, desired_secret, 0o600)
            atomic_write(DROPIN, desired_drop, 0o644)
            system("daemon-reload")
            system("restart", SERVICE)
            if not wait_for(lambda: healthy() and runtime_matches(key)):
                raise RuntimeError("RUNTIME_VERIFICATION_FAILED")
        except Exception:
            try:
                restore(SECRET, old_secret)
                restore(DROPIN, old_drop)
                system("daemon-reload")
                system("restart", SERVICE)
                if not wait_for(healthy):
                    raise RuntimeError()
            except Exception:
                raise RuntimeError("EXA_ROLLBACK_REQUIRES_ADMIN") from None
            raise RuntimeError("EXA_ROLLED_BACK_API_HEALTHY") from None
        return "EXA_ACTIVE_API_HEALTHY"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Apply local configuration; otherwise validate only.")
    args = parser.parse_args()
    key = os.environ.get("EXA_API_KEY", "").strip()
    try:
        print(provision(key, args.apply))
        return 0
    except RuntimeError as exc:
        marker = str(exc)
        print(marker if re.fullmatch(r"[A-Z_0-9]+", marker) else "EXA_SETUP_FAILED")
    except Exception:
        print("EXA_SETUP_FAILED")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
