#!/usr/bin/env python3
"""ServGo BookingBot independent, read-only SOCKS5-to-Telegram egress monitor.

The SSH service may be "active" even while individual SOCKS5 forwarding attempts
are broken. This checker makes a real HTTPS request through that exact proxy.
It never sends a bot token, restarts a service, or prints the curl error body.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time

PROXY = "socks5h://127.0.0.1:1089"
URL = "https://api.telegram.org/"
RELAY = ("89.125.90.101", 22)
STATUS_PATH = Path("/var/lib/servgo-socks5-health/status.json")
MAX_FAILURE_STREAK = 1_000_000


def utc_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def curl_probe():
    # -q must be the first curl argument to ignore potentially unsafe .curlrc.
    command = [
        "curl", "-q", "-4", "--silent", "--proxy", PROXY, "--noproxy", "",
        "--proto", "=https", "--connect-timeout", "4", "--max-time", "8",
        "--output", "/dev/null", "--write-out", "%{http_code} %{time_total}",
        URL,
    ]
    start = time.monotonic()
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=11, check=False)
        parts = result.stdout.strip().split()
        http = parts[0] if parts and re.fullmatch(r"[0-9]{3}", parts[0]) else "000"
        # Do not let unexpected curl output leak into the persisted state.
        seconds = float(parts[1]) if len(parts) > 1 else time.monotonic() - start
        duration_ms = min(999999, max(0, int(seconds * 1000)))
        return {
            "ok": result.returncode == 0 and http == "302",
            "http_code": http, "curl_exit": result.returncode,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "http_code": "000", "curl_exit": 124,
                "duration_ms": int((time.monotonic() - start) * 1000)}
    except (FileNotFoundError, OSError, ValueError, OverflowError):
        return {"ok": False, "http_code": "000", "curl_exit": 127,
                "duration_ms": int((time.monotonic() - start) * 1000)}


def reachable(address, timeout=2):
    try:
        with socket.create_connection(address, timeout=timeout):
            return True
    except OSError:
        return False


def service_active():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", "booking-bot-relay89.service"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3,
            check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def diagnose(last):
    # These are separate read-only checks, not automatically proven root causes.
    local = reachable(("127.0.0.1", 1089))
    ssh_service = service_active()
    relay_port = reachable(RELAY)
    if not local:
        reason = "local_socks_listener_unavailable"
    elif not ssh_service:
        reason = "ssh_tunnel_service_inactive"
    elif not relay_port:
        reason = "relay_ssh_tcp_unreachable"
    elif last["curl_exit"] == 28:
        reason = "telegram_socks_timeout"
    elif last["curl_exit"] != 0:
        reason = "telegram_socks_curl_error"
    else:
        reason = "unexpected_telegram_http_status"
    return {"reason": reason, "local_socks_listener": local,
            "tunnel_service_active": ssh_service, "relay_ssh_tcp": relay_port}


def load_previous(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def write_status(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    handle, tmp = tempfile.mkstemp(prefix=".status-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            os.fchmod(file.fileno(), 0o640)
            json.dump(data, file, ensure_ascii=True, sort_keys=True)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def run_check(probe=curl_probe, sleep=time.sleep, path=STATUS_PATH, now=utc_now,
              diagnose_failure=diagnose):
    previous = load_previous(path)
    attempts = [probe()]
    if not attempts[0]["ok"]:
        sleep(1)
        attempts.append(probe())
    last = attempts[-1]
    ok = any(attempt["ok"] for attempt in attempts)
    old_streak = previous.get("consecutive_failures", 0)
    if not isinstance(old_streak, int) or isinstance(old_streak, bool) or old_streak < 0:
        old_streak = 0
    streak = 0 if ok else min(MAX_FAILURE_STREAK, old_streak + 1)
    state = "up" if ok else ("suspect" if streak == 1 else "down")
    old_state = previous.get("state", "unknown")
    timestamp = now()
    result = {
        "check": "booking-socks5-telegram", "target": "https://api.telegram.org/",
        "proxy": PROXY, "state": state, "checked_at": timestamp,
        "consecutive_failures": streak, "attempts": len(attempts),
        "http_code": last["http_code"], "curl_exit": last["curl_exit"],
        "duration_ms": last["duration_ms"],
        "last_success_at": timestamp if ok else previous.get("last_success_at"),
        "first_failure_at": (timestamp if old_streak == 0 else
                             previous.get("first_failure_at", timestamp)) if not ok else None,
    }
    if not ok:
        result.update(diagnose_failure(last))
    else:
        result["reason"] = "ok"
    if old_state != state:
        result["last_state_change_at"] = timestamp
    else:
        result["last_state_change_at"] = previous.get("last_state_change_at", timestamp)
    write_status(path, result)
    # A single safe structured line per minute, with no IP credentials, tokens or headers.
    print("SOCKS5_TELEGRAM_CHECK " +
          " ".join(str(result[k]) for k in
                   ("state", "reason", "http_code", "curl_exit", "duration_ms",
                    "attempts", "consecutive_failures")), flush=True)
    if old_state in ("down", "suspect") and state == "up":
        print("SOCKS5_TELEGRAM_RECOVERED at=" + timestamp, flush=True)
    elif old_state != "down" and state == "down":
        print("SOCKS5_TELEGRAM_OUTAGE at=" + timestamp, flush=True)
    return result


 
def stateless_check(probe=curl_probe, sleep=time.sleep, diagnose_failure=diagnose):
    """Read-only check for GitHub Actions when installing on-host timer is unavailable."""
    attempts = [probe()]
    if not attempts[0]["ok"]:
        sleep(1)
        attempts.append(probe())
    last = attempts[-1]
    ok = any(item["ok"] for item in attempts)
    reason = "ok" if ok else diagnose_failure(last)["reason"]
    print("SOCKS5_TELEGRAM_STATELESS " +
          " ".join(str(value) for value in
                   ("up" if ok else "down", reason, last["http_code"],
                    last["curl_exit"], last["duration_ms"], len(attempts))),
          flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--stateless"]:
            sys.exit(stateless_check())
        if sys.argv[1:]:
            raise ValueError("Unknown monitor argument")
        run_check()
    except Exception as exc:
        # Internal instrumentation failures must fail the systemd unit; never show
        # exception messages (they may contain private paths or external content).
        print("SOCKS5_TELEGRAM_MONITOR_ERROR type=" + type(exc).__name__,
              file=sys.stderr, flush=True)
        sys.exit(1)
