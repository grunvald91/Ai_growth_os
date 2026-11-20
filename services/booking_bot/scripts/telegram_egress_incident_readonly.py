#!/usr/bin/env python3
"""One-off read-only diagnostics for the 2026-11-20 ServGo SOCKS5 incident.

Runs via GitHub Actions with pre-existing, pinned SSH credentials. No service
restart, no remote file creation, no credential, raw journal or customer data output.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
import time

import paramiko

logging.getLogger("paramiko").setLevel(logging.CRITICAL)
HOSTS = (
    ("servgo155", "155.212.162.47", "SERVER_PASSWORD",
     "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"),
    ("relay89", "89.125.90.101", "RELAY_PASSWORD",
     "+SqoDNS4YTI9OKHifsEpbLwWof6oXONbdFX+GLSTlC0"),
)

REMOTE = r'''
import json
import pathlib
import re
import socket
import subprocess
import sys

role = sys.argv[1]
print("ROLE=" + role, flush=True)
since = "2026-11-20 20:10:00 UTC"
until = "2026-11-20 20:20:00 UTC"

def run(args, timeout=12):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout
    except Exception:
        return 255, ""

def journal(unit):
    rc, payload = run(["journalctl", "-u", unit, "--since", since, "--until", until,
                       "--utc", "--no-pager", "-o", "json"], 14)
    events = []
    if rc != 0:
        print("JOURNAL_" + unit + "=unavailable", flush=True)
        return
    for line in payload.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        msg = str(d.get("MESSAGE", ""))
        if not msg:
            continue
        found = []
        for tag,pattern in (
            ("timeout", r"timed? ?out|timeout"),
            ("disconnect", r"disconnected|connection (?:closed|reset)|broken pipe|remote host closed"),
            ("reconnect", r"connected|connection established|authenticated"),
            ("failure", r"failed|failure|fatal|error|connection refused"),
            ("exit", r"main process exited|stopped|stopping|restart|started"),
            ("polling", r"start polling|run polling for bot"),
        ):
            if re.search(pattern, msg, re.I):
                found.append(tag)
        if found:
            stamp = int(d.get("__REALTIME_TIMESTAMP", 0)) // 1000000
            events.append((stamp, ",".join(found)))
    kinds = sorted(set(kind for _, tags in events for kind in tags.split(",")))
    print("JOURNAL_" + unit + "_MATCHES=" + str(len(events)))
    for kind in kinds:
        print("JOURNAL_" + unit + "_" + kind + "=" +
              str(sum(kind in tags.split(",") for _,tags in events)))
    for stamp, tags in events[-25:]:
        print("EVENT_" + unit + "=" + str(stamp) + ":" + tags)

def state(unit):
    rc, body = run(["systemctl", "show", unit,
                    "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                    "-p", "NRestarts", "-p", "Result",
                    "-p", "ActiveEnterTimestamp", "-p", "ExecMainStartTimestamp"], 8)
    allow = {"LoadState", "ActiveState", "SubState", "NRestarts",
             "Result", "ActiveEnterTimestamp", "ExecMainStartTimestamp"}
    parts = {}
    for line in body.splitlines():
        if "=" in line:
            k,v = line.split("=",1)
            if k in allow:
                parts[k] = v[:80]
    print("SYSTEMD_" + unit + "=" + json.dumps(parts, sort_keys=True), flush=True)

def probe(via_proxy):
    command = ["curl", "-4", "--silent", "--output", "/dev/null",
               "--connect-timeout", "4", "--max-time", "10",
               "--write-out", "http=%{http_code} connect=%{time_connect} tls=%{time_appconnect} total=%{time_total}"]
    if via_proxy:
        command.extend(["--proxy", "socks5h://127.0.0.1:1089", "--noproxy", ""])
    else:
        command.extend(["--noproxy", "*"])
    command.append("https://api.telegram.org/")
    for n in range(3):
        rc, out = run(command, 13)
        print(("SOCKS" if via_proxy else "DIRECT") + "_PROBE_" + str(n + 1)
              + "=exit_" + str(rc) + "," + out.strip()[:140], flush=True)

if role == "servgo155":
    for unit in ("booking-bot-relay89.service", "booking-bot.service", "booking-web-api.service"):
        state(unit)
        journal(unit)
    rc, ss = run(["ss", "-H", "-lnt"], 8)
    print("SOCKS_LOOPBACK_LISTENER_1089=" +
          str(any("127.0.0.1:1089" in row for row in ss.splitlines())).lower())
    try:
        with socket.create_connection(("89.125.90.101", 22), timeout=5):
            print("SERVGO_TO_RELAY_SSH_TCP=connected")
    except OSError as exc:
        print("SERVGO_TO_RELAY_SSH_TCP=" + type(exc).__name__)
    probe(True)
else:
    for unit in ("ssh.service", "sshd.service", "telemt-direct-ozon.service",
                 "wg-quick@tg-relay.service"):
        state(unit)
    # Only aggregates, never expose raw SSH authentication lines.
    rc, sshlog = run(["journalctl", "-u", "ssh.service", "-u", "sshd.service",
                      "--since", since, "--until", until,
                      "--no-pager", "-o", "cat"], 12)
    matched = [line for line in sshlog.splitlines() if "155.212.162.47" in line]
    print("RELAY_SSH_FROM_SERVGO_EVENTS=" + str(len(matched)))
    for name, pattern in (
        ("opened", r"accepted|session opened"),
        ("closed", r"closed|disconnect|reset|broken pipe"),
        ("error", r"error|failed|timeout"),
    ):
        print("RELAY_SSH_FROM_SERVGO_" + name + "=" +
              str(sum(bool(re.search(pattern, line, re.I)) for line in matched)))
    probe(False)

rc, kernel = run(["journalctl", "-k", "--since", since, "--until", until,
                  "--no-pager", "-o", "cat"], 12)
for tag, pattern in (
    ("oom", r"out of memory|oom.killer|killed process"),
    ("conntrack", r"nf_conntrack.*(?:full|drop)"),
    ("network", r"NETDEV WATCHDOG|link is down|network is unreachable"),
):
    print("KERNEL_" + tag.upper() + "_MATCHES=" +
          str(len(re.findall(pattern, kernel, re.I))))
print("READ_ONLY_COMPLETE=true", flush=True)
'''

class PinnedHost(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected: str):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if key.get_name() != "ssh-ed25519" or actual != self.expected:
            raise paramiko.SSHException("Pinned host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)

def main():
    failures = []
    for role, host, env_var, fingerprint in HOSTS:
        secret = os.environ.get(env_var)
        if not secret:
            print(role + "=credential_unavailable")
            failures.append(role)
            continue
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHost(fingerprint))
        try:
            client.connect(host, username="root", password=secret, port=22,
                           look_for_keys=False, allow_agent=False,
                           timeout=12, auth_timeout=15, banner_timeout=25)
            stdin, stdout, stderr = client.exec_command(
                "python3 - " + role, timeout=95)
            stdin.write(REMOTE)
            stdin.channel.shutdown_write()
            body = stdout.read().decode("utf-8", "replace")
            rc = stdout.channel.recv_exit_status()
            print(body[:28000], flush=True)
            if rc:
                print(role + "=diagnostic_failed_exit_" + str(rc))
                failures.append(role)
        except Exception as exc:
            print(role + "=unavailable_" + type(exc).__name__)
            failures.append(role)
        finally:
            client.close()
    print("UNREACHABLE_NODES=" + ",".join(failures))
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(main())
