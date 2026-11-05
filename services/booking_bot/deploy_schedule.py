#!/usr/bin/env python3
"""Upload schedule-related files and restart the configured service."""
import os
import sys

import paramiko

from deployment_settings import PROJECT_ROOT, require_deployment_settings


sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SETTINGS = require_deployment_settings()
REMOTE_BASE = SETTINGS.remote_path
LOCAL_BASE = str(PROJECT_ROOT)

FILES = [
    "webapp/server.py",
    "webapp/templates/analytics.html",
    "webapp/templates/schedule.html",
    "webapp/static/js/analytics.js",
    "webapp/static/js/schedule.js",
    "webapp/static/css/analytics.css",
    "webapp/static/css/schedule.css",
    "bot/keyboards/admin_kb.py",
]

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(
    SETTINGS.host,
    port=SETTINGS.port,
    username=SETTINGS.user,
    password=SETTINGS.password,
)
sftp = ssh.open_sftp()

print("Connected to server")

for file_path in FILES:
    full_local = os.path.join(LOCAL_BASE, file_path)
    full_remote = f"{REMOTE_BASE}/{file_path}"

    remote_dir = os.path.dirname(full_remote)
    try:
        sftp.stat(remote_dir)
    except OSError:
        stdin, stdout, stderr = ssh.exec_command(f"mkdir -p {remote_dir}")
        stdout.read()

    sftp.put(full_local, full_remote)
    print(f"Uploaded: {file_path}")

print(f"Restarting {SETTINGS.service_name} service...")
stdin, stdout, stderr = ssh.exec_command(f"systemctl restart {SETTINGS.service_name}")
exit_code = stdout.channel.recv_exit_status()
err = stderr.read().decode("utf-8", errors="replace")
if exit_code == 0:
    print("Service restarted successfully")
else:
    print(f"Restart error: {err}")

sftp.close()
ssh.close()
print("Done!")
