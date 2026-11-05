#!/usr/bin/env python3
"""Restart the configured bot service on the server."""
import time

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

    print("Stopping bot...")
    stdin, stdout, stderr = ssh.exec_command(f"systemctl stop {SETTINGS.service_name}")
    stdout.channel.recv_exit_status()
    print("Bot stopped")

    time.sleep(2)

    print("Starting bot...")
    stdin, stdout, stderr = ssh.exec_command(f"systemctl start {SETTINGS.service_name}")
    stdout.channel.recv_exit_status()
    print("Bot started")

    time.sleep(2)

    print("\nChecking bot status...")
    stdin, stdout, stderr = ssh.exec_command(f"systemctl status {SETTINGS.service_name}")
    print(stdout.read().decode())

    print("\nRecent logs:")
    stdin, stdout, stderr = ssh.exec_command(
        f"journalctl -u {SETTINGS.service_name} -n 20 --no-pager"
    )
    print(stdout.read().decode())

    ssh.close()

    print("\nBot restarted successfully!")
except Exception as exc:
    print(f"Error: {exc}")
    raise SystemExit(1)
