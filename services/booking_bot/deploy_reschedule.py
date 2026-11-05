#!/usr/bin/env python3
"""Upload reschedule-related files and restart the configured service."""
import os
import time

import paramiko

from deployment_settings import PROJECT_ROOT, require_deployment_settings


SETTINGS = require_deployment_settings()
BASE = PROJECT_ROOT

FILES_TO_UPLOAD = [
    (os.path.join(BASE, "webapp", "server.py"), "/opt/booking_bot/webapp/server.py"),
    (os.path.join(BASE, "webapp", "static", "js", "booking.js"), "/opt/booking_bot/webapp/static/js/booking.js"),
    (os.path.join(BASE, "webapp", "templates", "booking.html"), "/opt/booking_bot/webapp/templates/booking.html"),
    (os.path.join(BASE, "webapp", "static", "css", "booking.css"), "/opt/booking_bot/webapp/static/css/booking.css"),
]


try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {SETTINGS.host}...")
    ssh.connect(
        SETTINGS.host,
        port=SETTINGS.port,
        username=SETTINGS.user,
        password=SETTINGS.password,
        timeout=30,
    )

    sftp = ssh.open_sftp()
    for local_path, remote_path in FILES_TO_UPLOAD:
        print(f"Uploading {local_path} -> {remote_path}")
        sftp.put(local_path, remote_path)
        print("  [OK]")

    sftp.close()

    print(f"\nRestarting {SETTINGS.service_name} service...")
    stdin, stdout, stderr = ssh.exec_command(f"systemctl restart {SETTINGS.service_name}")
    exit_code = stdout.channel.recv_exit_status()
    print(f"Restart exit code: {exit_code}")

    time.sleep(3)

    print("\nChecking status...")
    stdin, stdout, stderr = ssh.exec_command(
        f"systemctl status {SETTINGS.service_name} --no-pager -l"
    )
    print(stdout.read().decode())

    ssh.close()
    print("Deploy completed!")
except Exception as exc:
    print(f"Error: {exc}")
    import traceback

    traceback.print_exc()
