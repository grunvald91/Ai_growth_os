"""Manually deploy only ServGo bot SOCKS transport with pinned host and backups."""
import base64
import hashlib
import logging
import os
from pathlib import Path
import shlex
import time

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
BASELINE = "ee1de3823a4548d97323d405dd44e60da7a7ae49cd60e03cfa9750654b1b3846"
CLIENT_BASELINE = "0c248d71a7556409d07a96e12639c0310a6e98b8a54aac8e013d2724cee89b18"
MAIN = ROOT + "/bot/main.py"
CLIENT = ROOT + "/bot/handlers/client.py"
TRANSPORT = ROOT + "/bot/telegram_transport.py"
DROPIN = "/etc/systemd/system/booking-bot.service.d/relay89.conf"
DROPIN_TEXT = """[Unit]
Requires=booking-bot-relay89.service
After=booking-bot-relay89.service

[Service]
Environment=BOT_PROXY_URL=socks5://127.0.0.1:1089
"""
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def run(client, command, timeout=40):
    _, output, _ = client.exec_command(command, timeout=timeout)
    body = output.read().decode("utf-8", "replace").strip()
    if output.channel.recv_exit_status():
        raise RuntimeError("Deployment remote command failed; stderr suppressed")
    return body


def put_new(sftp, name, content, mode=0o644):
    with sftp.open(name, "wx") as file:
        file.write(content)
    sftp.chmod(name, mode)


def getme(client):
    code = """import asyncio
from bot.config import BOT_TOKEN
from bot.telegram_transport import create_bot
async def verify():
    bot=create_bot(BOT_TOKEN,'socks5://127.0.0.1:1089')
    try:
        me=await bot.get_me()
        print('bot_api_getMe_ok='+str(bool(me.id and me.username)))
        print('bot_username='+str(me.username))
    finally:
        await bot.session.close()
asyncio.run(verify())"""
    return run(client, "cd /opt/booking_bot && ./venv/bin/python -c " + shlex.quote(code), timeout=35)


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing SERVER_SSH_PASSWORD GitHub secret")
    client = None
    for attempt in range(3):
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(PinnedKey())
        try:
            c.connect(HOST, port=22, username="root", password=password, allow_agent=False,
                      look_for_keys=False, timeout=12, banner_timeout=25, auth_timeout=15)
            client = c
            break
        except Exception:
            c.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)
    sftp = client.open_sftp()
    deployed = False
    backup = None
    client_backup = None
    try:
        if run(client, "systemctl is-active booking-bot-relay89.service") != "active":
            raise RuntimeError("Private SSH tunnel is not active")
        probe = "curl -4 -sS --proxy socks5h://127.0.0.1:1089 --noproxy '' --max-time 12 -o /dev/null -w '%{http_code}' https://api.telegram.org/"
        if run(client, probe, timeout=17) != "302":
            raise RuntimeError("Bot API SOCKS route has stopped working")
        with sftp.open(MAIN, "rb") as file:
            original = file.read()
        if hashlib.sha256(original).hexdigest() != BASELINE:
            raise RuntimeError("Production main.py differs from reviewed baseline; refusing overwrite")
        with sftp.open(CLIENT, "rb") as file:
            original_client = file.read()
        if hashlib.sha256(original_client).hexdigest() != CLIENT_BASELINE:
            raise RuntimeError("Production client.py differs from reviewed baseline; refusing overwrite")
        try:
            sftp.stat(TRANSPORT)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("Unexpected existing bot transport file")
        try:
            sftp.stat(DROPIN)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("Unexpected existing bot proxy drop-in")
        new_main = Path("bot/main.py").read_bytes()
        new_client = Path("bot/handlers/client.py").read_bytes()
        new_transport = Path("bot/telegram_transport.py").read_bytes()
        if b'create_bot(BOT_TOKEN, os.getenv("BOT_PROXY_URL"))' not in new_main:
            raise RuntimeError("Local bot main missing expected proxy configuration")
        if b'AiohttpSession(proxy=proxy_url)' not in new_transport:
            raise RuntimeError("Local transport missing SOCKS support")
        if b'get_mode_switch_keyboard(show_admin_button=' in new_client:
            raise RuntimeError("Local booking keyboard callsites still have incompatible arguments")
        # Dependency install precedes all source changes.
        run(client, ROOT + "/venv/bin/python -m pip install --disable-pip-version-check --no-input 'aiohttp-socks==0.10.2' >/dev/null", timeout=160)
        run(client, ROOT + "/venv/bin/python -c 'import aiohttp_socks; print(\"socks_dependency_ok=yes\")'")
        backup = MAIN + ".bak.relay89." + str(int(time.time()))
        client_backup = CLIENT + ".bak.relay89." + str(int(time.time()))
        run(client, "cp -p " + shlex.quote(MAIN) + " " + shlex.quote(backup))
        run(client, "cp -p " + shlex.quote(CLIENT) + " " + shlex.quote(client_backup))
        deployed = True
        put_new(sftp, TRANSPORT, new_transport)
        client_temp = CLIENT + ".relay89.tmp"
        put_new(sftp, client_temp, new_client, sftp.stat(CLIENT).st_mode & 0o777)
        sftp.posix_rename(client_temp, CLIENT)
        temp = MAIN + ".relay89.tmp"
        put_new(sftp, temp, new_main, sftp.stat(MAIN).st_mode & 0o777)
        sftp.posix_rename(temp, MAIN)
        put_new(sftp, DROPIN, DROPIN_TEXT.encode())
        run(client, "cd /opt/booking_bot && ./venv/bin/python -m py_compile bot/main.py bot/handlers/client.py bot/telegram_transport.py")
        result = getme(client)
        if "bot_api_getMe_ok=True" not in result:
            raise RuntimeError("Bot API getMe did not succeed through SOCKS")
        print(result)
        run(client, "systemctl daemon-reload && systemctl reset-failed booking-bot.service && systemctl start booking-bot.service", timeout=35)
        for check in range(3):
            time.sleep(12)
            state = run(client, "systemctl is-active booking-bot.service")
            if state != "active":
                raise RuntimeError("Bot stopped during polling verification")
            print("bot_active_check_" + str(check + 1) + "=yes")
        # No raw logs: bot logs customer messages, callback data and errors with sensitive context.
        inspect = """import subprocess
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','2 minutes ago','--no-pager','-o','cat'],capture_output=True,text=True,timeout=8)
lines=p.stdout.splitlines()
print('polling_start_log_count='+str(sum('Start polling' in s or 'Run polling for bot' in s for s in lines)))
print('telegram_network_error_count='+str(sum('TelegramNetworkError' in s or 'ClientConnectorError' in s for s in lines)))
print('traceback_count='+str(sum(s.startswith('Traceback (most recent call last):') for s in lines)))
print('polling_healthy='+str(any('Run polling for bot' in s for s in lines) and not any('TelegramNetworkError' in s or 'ClientConnectorError' in s for s in lines)))"""
        report = run(client, "/opt/booking_bot/venv/bin/python -c " + shlex.quote(inspect))
        print(report)
        if "polling_healthy=True" not in report:
            raise RuntimeError("Bot polling health proof failed")
        print("bot_relay_deployed=yes")
    except Exception:
        if deployed:
            # The bot was failed before rollout: restore code/drop-in, do not restart direct connectivity.
            try:
                run(client, "set -e; systemctl stop booking-bot.service; cp -p " + shlex.quote(backup) + " " + shlex.quote(MAIN)
                    + "; cp -p " + shlex.quote(client_backup) + " " + shlex.quote(CLIENT)
                    + "; rm -f " + shlex.quote(TRANSPORT) + " " + shlex.quote(DROPIN) + " " + shlex.quote(MAIN + '.relay89.tmp') + " " + shlex.quote(CLIENT + '.relay89.tmp')
                    + "; systemctl daemon-reload; systemctl reset-failed booking-bot.service")
                print("bot_source_and_unit_rolled_back=yes")
            except Exception:
                print("rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
