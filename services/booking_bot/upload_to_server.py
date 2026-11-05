#!/usr/bin/env python3
"""Upload selected files to the configured server via SFTP."""
import paramiko

from deployment_settings import PROJECT_ROOT, require_deployment_settings


SETTINGS = require_deployment_settings()

FILES_TO_UPLOAD = [
    ("bot/handlers/client.py", "/opt/booking_bot/bot/handlers/client.py"),
    ("bot/keyboards/client_kb.py", "/opt/booking_bot/bot/keyboards/client_kb.py"),
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
    )

    sftp = ssh.open_sftp()

    for local_path, remote_path in FILES_TO_UPLOAD:
        print(f"Uploading {local_path} to {remote_path}...")
        sftp.put(str(PROJECT_ROOT / local_path), remote_path)
        print(f"[OK] {local_path} uploaded successfully")

    sftp.close()
    ssh.close()

    print("\nAll files uploaded successfully!")
except Exception as exc:
    print(f"Error: {exc}")
    raise SystemExit(1)
