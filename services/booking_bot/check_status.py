#!/usr/bin/env python3
"""Print process, service state, and recent logs from the deployment server."""
import paramiko
import sys

from deployment_settings import require_deployment_settings


sys.stdout.reconfigure(encoding="utf-8")
SETTINGS = require_deployment_settings()

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    SETTINGS.host,
    port=SETTINGS.port,
    username=SETTINGS.user,
    password=SETTINGS.password,
    timeout=30,
)

print("Bot process:")
stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python.*bot.main' | grep -v grep")
print(stdout.read().decode("utf-8"))

print("\nService status (active/inactive):")
stdin, stdout, stderr = ssh.exec_command(f"systemctl is-active {SETTINGS.service_name}")
print(stdout.read().decode("utf-8"))

print("\nRecent logs:")
stdin, stdout, stderr = ssh.exec_command(
    f"journalctl -u {SETTINGS.service_name} -n 15 --no-pager 2>&1"
)
output = stdout.read().decode("utf-8", errors="replace")
for line in output.split("\n"):
    clean = "".join(char if ord(char) < 128 else "?" for char in line)
    print(clean)

ssh.close()
