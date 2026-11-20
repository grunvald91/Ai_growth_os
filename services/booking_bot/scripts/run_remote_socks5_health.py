#!/usr/bin/env python3
"""Read-only GitHub Actions check: run the same SOCKS5 probe on ServGo155.

Transmits the probe over authenticated SSH stdin and runs with --stateless:
no file installation, service restart, config change or remote state writes.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from pathlib import Path
import re
import sys

import paramiko

logging.getLogger("paramiko").setLevel(logging.CRITICAL)
HOST = "155.212.162.47"
HOST_FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
SOURCE = Path(__file__).with_name("socks5_telegram_health.py")


class PinnedHost(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if key.get_name() != "ssh-ed25519" or fingerprint != HOST_FP:
            raise paramiko.SSHException("Pinned ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def main():
    password = os.environ.get("SERVER_PASSWORD")
    if not password:
        print("SOCKS5_MONITOR_TRANSPORT=missing_credential", flush=True)
        return 2
    source = SOURCE.read_text(encoding="utf-8")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHost())
    try:
        try:
            client.connect(HOST, username="root", password=password, port=22,
                           allow_agent=False, look_for_keys=False, timeout=12,
                           auth_timeout=15, banner_timeout=25)
        except Exception as exc:
            print("SOCKS5_MONITOR_TRANSPORT=" + type(exc).__name__, flush=True)
            return 2
        stdin, stdout, _ = client.exec_command("python3 - --stateless", timeout=48)
        stdin.write(source)
        stdin.channel.shutdown_write()
        data = stdout.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
        # Filter output to an expected, structured, safe one-line result.
        allowed = [line for line in data.splitlines()
                   if re.fullmatch(r"SOCKS5_TELEGRAM_STATELESS (?:up|down) "
                                   r"[a-z_]+ [0-9]{3} [0-9]{1,3} "
                                   r"[0-9]{1,9} [12]", line)]
        if len(allowed) != 1 or rc not in (0, 1):
            print("SOCKS5_MONITOR_TRANSPORT=unexpected_remote_result", flush=True)
            return 2
        print(allowed[0], flush=True)
        print("SOCKS5_MONITOR_RESULT=" + ("ok" if rc == 0 else "outage"), flush=True)
        return rc
    except Exception as exc:
        print("SOCKS5_MONITOR_TRANSPORT=" + type(exc).__name__, flush=True)
        return 2
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
