#!/usr/bin/env python3
"""Show recent service logs from the configured deployment server."""
import paramiko

from deployment_settings import require_deployment_settings


SETTINGS = require_deployment_settings()


try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"Connecting to {SETTINGS.host}...")
    ssh.connect(
        SETTINGS.host,
        port=SETTINGS.port,
        username=SETTINGS.user,
        password=SETTINGS.password,
    )

    print("Recent logs:")
    stdin, stdout, stderr = ssh.exec_command(
        f"journalctl -u {SETTINGS.service_name} -n 30 --no-pager"
    )
    print(stdout.read().decode("utf-8", errors="replace"))

    ssh.close()
except Exception as exc:
    print(f"Error: {exc}")
    raise SystemExit(1)
