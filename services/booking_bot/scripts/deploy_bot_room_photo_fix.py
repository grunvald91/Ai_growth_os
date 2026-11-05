"""Manual, scoped ServGo bot room-photo patch; preserves unrelated production code."""
import ast
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
ADMIN = "/opt/booking_bot/bot/handlers/admin.py"
SERVICE = "booking-bot.service"
BASELINE = {
    "edit_emp_photos_menu": "ca7fce86bcac6dbfff2138137e243853f433bdc4a965fbc2de009d731026d5e8",
    "emp_add_photo_start": "e4eeb7e9f001690cbf5820a174a62ba911f2b04a6bb46ae567fa0ae5bccd4486",
    "emp_add_photo_receive": "de722cac1463f44b2f01a1a97aefa44014fcaa813d0ad15176a8640d245cb7b8",
    "emp_photos_done": "dba2c3476b929b1f2acb3b029e5daddb4490c535f66054d02af0f542791019fd",
}
OLD_IMPORT = "from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove"
NEW_IMPORT = OLD_IMPORT + ", InlineKeyboardMarkup, InlineKeyboardButton"
VIEW_MARKER = '@router.callback_query(F.data.startswith("emp_view_photos:"))'
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


def nodes(source):
    return {node.name: node for node in ast.parse(source).body if isinstance(node, ast.AsyncFunctionDef)}


def patch_source(original, new, baseline=BASELINE):
    current_nodes = nodes(original)
    updated_nodes = nodes(new)
    lines = original.splitlines(keepends=True)
    for name, digest in sorted(baseline.items(), key=lambda item: current_nodes[item[0]].lineno, reverse=True):
        old_node = current_nodes[name]
        new_node = updated_nodes[name]
        old_body = ast.get_source_segment(original, old_node)
        if hashlib.sha256(old_body.encode()).hexdigest() != digest:
            raise RuntimeError("Production room-photo handler baseline mismatch: " + name)
        new_body = ast.get_source_segment(new, new_node)
        lines[old_node.lineno - 1:old_node.end_lineno] = [new_body + "\n"]
    result = "".join(lines)
    if result.count(OLD_IMPORT + "\n") != 1:
        raise RuntimeError("Production aiogram import baseline mismatch")
    result = result.replace(OLD_IMPORT + "\n", NEW_IMPORT + "\n", 1)
    if result.count(VIEW_MARKER) != 1:
        raise RuntimeError("Production room-photo view marker mismatch")
    new_node = updated_nodes["emp_photos_back"]
    new_handler = "\n".join(new.splitlines()[new_node.decorator_list[0].lineno - 1:new_node.end_lineno])
    result = result.replace(VIEW_MARKER, new_handler + "\n\n\n" + VIEW_MARKER, 1)
    ast.parse(result)
    return result


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def run(client, command, timeout=40):
    _, out, _ = client.exec_command(command, timeout=timeout)
    body = out.read().decode("utf-8", "replace").strip()
    if out.channel.recv_exit_status():
        raise RuntimeError("Remote command failed; stderr suppressed")
    return body


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing ServGo GitHub SSH secret")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedKey())
    try:
        client.connect(HOST, port=22, username="root", password=password, allow_agent=False,
                       look_for_keys=False, timeout=12, banner_timeout=25, auth_timeout=15)
        sftp = client.open_sftp()
        backup = None
        changed = False
        tmp = ADMIN + ".room-photo.tmp"
        try:
            if run(client, "systemctl is-active " + SERVICE) != "active":
                raise RuntimeError("Bot not active before rollout")
            if run(client, "systemctl is-active booking-bot-relay89.service") != "active":
                raise RuntimeError("Private Bot API tunnel inactive")
            with sftp.open(ADMIN, "rb") as f:
                original = f.read()
            if b"\r\n" in original:
                raise RuntimeError("Unexpected production newline convention")
            amended = patch_source(original.decode("utf-8"), Path("bot/handlers/admin.py").read_text(encoding="utf-8"))
            if amended.encode() == original:
                raise RuntimeError("No room-photo changes to deploy")
            backup = ADMIN + ".bak.room-photo." + str(int(time.time()))
            run(client, "cp -p " + shlex.quote(ADMIN) + " " + shlex.quote(backup))
            with sftp.open(tmp, "wx") as file:
                file.write(amended.encode("utf-8"))
            sftp.chmod(tmp, sftp.stat(ADMIN).st_mode & 0o777)
            sftp.posix_rename(tmp, ADMIN)
            changed = True
            run(client, "cd /opt/booking_bot && ./venv/bin/python -m py_compile bot/handlers/admin.py")
            run(client, "systemctl restart " + SERVICE, timeout=45)
            for attempt in range(3):
                time.sleep(8)
                if run(client, "systemctl is-active " + SERVICE) != "active":
                    raise RuntimeError("Bot inactive after room-photo patch")
                print("bot_active_check_" + str(attempt + 1) + "=yes")
            check = """import subprocess
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','2 minutes ago','--no-pager','-o','cat'],capture_output=True,text=True,timeout=8)
s=p.stdout
print('polling_healthy='+str('Run polling for bot' in s and 'TelegramNetworkError' not in s))
print('error_count='+str(s.count('Traceback (most recent call last):')))"""
            health = run(client, "/opt/booking_bot/venv/bin/python -c " + shlex.quote(check))
            print(health)
            if "polling_healthy=True" not in health or "error_count=0" not in health:
                raise RuntimeError("Bot polling failed after room-photo patch")
            with sftp.open(ADMIN, "rb") as f:
                deployed = f.read()
            if hashlib.sha256(deployed).hexdigest() != hashlib.sha256(amended.encode()).hexdigest():
                raise RuntimeError("Deployed room-photo bytes differ")
            print("bot_room_photo_fix_deployed=yes")
        except Exception:
            if changed and backup:
                try:
                    run(client, "cp -p " + shlex.quote(backup) + " " + shlex.quote(ADMIN) + " && systemctl restart " + SERVICE, timeout=45)
                    print("bot_room_photo_rollback=yes")
                except Exception:
                    print("bot_room_photo_rollback_incomplete=yes")
            raise
        finally:
            try:
                sftp.remove(tmp)
            except IOError:
                pass
            sftp.close()
    except Exception:
        raise RuntimeError("ServGo room-photo deploy failed; details suppressed") from None
    finally:
        client.close()


if __name__ == "__main__":
    main()
