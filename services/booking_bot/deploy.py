#!/usr/bin/env python3
"""Create a deploy archive and upload it to the configured server."""
import os
import subprocess
import sys
import time

import paramiko

from deployment_settings import PROJECT_ROOT, require_deployment_settings


SETTINGS = require_deployment_settings()


def create_archive():
    """Create the deployment archive."""
    print("=" * 60)
    print("STEP 1: Creating deployment archive")
    print("=" * 60)

    result = subprocess.run(
        [sys.executable, "create_deploy_archive.py"],
        capture_output=True,
        text=True,
        check=False,
    )
    print(result.stdout)
    if result.returncode != 0:
        print("Error creating archive:", result.stderr)
        return False
    return True


def deploy():
    """Deploy the prepared archive to the remote server."""
    print("\n" + "=" * 60)
    print("STEP 2: Deploying to server")
    print("=" * 60)

    archive_name = "deploy_update.tar.gz"

    if not os.path.exists(archive_name):
        print(f"Error: {archive_name} not found!")
        return False

    print(f"\nConnecting to {SETTINGS.host}...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        ssh.connect(
            SETTINGS.host,
            port=SETTINGS.port,
            username=SETTINGS.user,
            password=SETTINGS.password,
            timeout=30,
        )
        print("Connected!")

        print("\nUploading archive...")
        sftp = ssh.open_sftp()
        sftp.put(archive_name, f"{SETTINGS.remote_path}/{archive_name}")
        sftp.close()
        print("Archive uploaded!")

        print("\nStopping bot service...")
        stdin, stdout, stderr = ssh.exec_command(f"systemctl stop {SETTINGS.service_name}")
        stdout.channel.recv_exit_status()
        print("Bot stopped.")

        print("\nCreating backup...")
        ssh.exec_command(
            f"cd {SETTINGS.remote_path} && "
            "cp -r bot bot.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true"
        )

        print("\nExtracting new files...")
        stdin, stdout, stderr = ssh.exec_command(
            f"cd {SETTINGS.remote_path} && tar -xzf {archive_name}"
        )
        stdout.channel.recv_exit_status()
        print("Files extracted.")

        ssh.exec_command(f"rm {SETTINGS.remote_path}/{archive_name}")

        print("\nRestarting bot service...")
        stdin, stdout, stderr = ssh.exec_command(f"systemctl restart {SETTINGS.service_name}")
        stdout.channel.recv_exit_status()

        time.sleep(3)

        print("\nChecking bot status...")
        stdin, stdout, stderr = ssh.exec_command('ps aux | grep "python.*bot.main" | grep -v grep')
        ps_output = stdout.read().decode().strip()

        if ps_output:
            print("\n" + "=" * 60)
            print("SUCCESS: Bot is running!")
            print("=" * 60)
            print(ps_output)
            return True

        print("\n" + "=" * 60)
        print("WARNING: Bot may not be running!")
        print("=" * 60)
        stdin, stdout, stderr = ssh.exec_command(
            f"journalctl -u {SETTINGS.service_name} -n 20 --no-pager"
        )
        logs = stdout.read().decode()
        print("\nLast 20 log lines:")
        print(logs)
        return False
    except Exception as exc:
        print(f"\nError: {exc}")
        import traceback

        traceback.print_exc()
        return False
    finally:
        ssh.close()


if __name__ == "__main__":
    os.chdir(PROJECT_ROOT)

    print("\n" + "=" * 60)
    print("BOOKING BOT DEPLOYMENT")
    print("=" * 60)

    if not create_archive():
        print("\nDeployment aborted: failed to create archive")
        sys.exit(1)

    if not deploy():
        print("\nDeployment completed with warnings")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("DEPLOYMENT COMPLETED SUCCESSFULLY!")
    print("=" * 60)
