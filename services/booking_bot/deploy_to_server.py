#!/usr/bin/env python3
"""Upload deploy_update.tar.gz to the configured server and restart the bot."""
import time

import paramiko

from deployment_settings import PROJECT_ROOT, require_deployment_settings


SETTINGS = require_deployment_settings()
LOCAL_ARCHIVE = PROJECT_ROOT / "deploy_update.tar.gz"


def deploy():
    print(f"Connecting to {SETTINGS.host}...")

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

        print("Uploading archive...")
        sftp = ssh.open_sftp()
        sftp.put(str(LOCAL_ARCHIVE), f"{SETTINGS.remote_path}/deploy_update.tar.gz")
        sftp.close()
        print("Archive uploaded!")

        print("\nStopping bot service...")
        stdin, stdout, stderr = ssh.exec_command(f"systemctl stop {SETTINGS.service_name}")
        stdout.channel.recv_exit_status()
        print("Bot stopped.")

        print("Creating backup...")
        stdin, stdout, stderr = ssh.exec_command(
            f"cd {SETTINGS.remote_path} && cp -r bot bot.backup 2>/dev/null || true"
        )
        stdout.channel.recv_exit_status()

        print("Extracting new files...")
        stdin, stdout, stderr = ssh.exec_command(
            f"cd {SETTINGS.remote_path} && tar -xzvf deploy_update.tar.gz"
        )
        output = stdout.read().decode()
        print(output)

        ssh.exec_command(f"rm {SETTINGS.remote_path}/deploy_update.tar.gz")

        print("\nRestarting bot service...")
        stdin, stdout, stderr = ssh.exec_command(f"systemctl start {SETTINGS.service_name}")
        stdout.channel.recv_exit_status()

        time.sleep(2)

        print("\nChecking bot status...")
        stdin, stdout, stderr = ssh.exec_command(f"systemctl status {SETTINGS.service_name}")
        print(stdout.read().decode())

        stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'python.*bot.main' | grep -v grep")
        ps_output = stdout.read().decode().strip()
        if ps_output:
            print("Bot is running!")
        else:
            print("Bot may not be running, check logs!")
            stdin, stdout, stderr = ssh.exec_command(
                f"journalctl -u {SETTINGS.service_name} -n 20 --no-pager"
            )
            print(stdout.read().decode())

        print("\n=== Deploy completed! ===")
    except Exception as exc:
        print(f"Error: {exc}")
        import traceback

        traceback.print_exc()
    finally:
        ssh.close()


if __name__ == "__main__":
    deploy()
