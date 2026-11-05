"""Manually deploy only ServGo bot room-photo album handling."""
import ast
import hashlib
import logging
import os
from pathlib import Path
import shlex
import time

import paramiko

from scripts.deploy_bot_room_photo_fix import ADMIN, HOST, PinnedKey, run, nodes

BASELINE = {
    "emp_add_photo_receive": "095d89564f2c2f26ee1239c2c6487403483bbe5f9526320f5f9f1f88a3e981c8",
    "emp_photos_done": "106f923143ce88b801ff7de97c1ad50f56cc389a0c89030c10b44b4ca89b021f",
    "emp_photos_back": "f3aaf9ad06260a51c7e083a14f38871d2caef8cda2efda7a309b5b05adddeae9",
}
IMPORT_MARKER = "from bot.utils.message_utils import safe_edit_or_send\n\nSUPER_ADMIN_ID"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


def patch_album_source(original, new, baseline=BASELINE):
    current_nodes = nodes(original)
    updated_nodes = nodes(new)
    lines = original.splitlines(keepends=True)
    for name, digest in sorted(baseline.items(), key=lambda item: current_nodes[item[0]].lineno, reverse=True):
        old_node = current_nodes[name]
        old_body = ast.get_source_segment(original, old_node)
        if hashlib.sha256(old_body.encode()).hexdigest() != digest:
            raise RuntimeError("Production album handler baseline mismatch: " + name)
        new_body = ast.get_source_segment(new, updated_nodes[name])
        lines[old_node.lineno - 1:old_node.end_lineno] = [new_body + "\n"]
    result = "".join(lines)
    if result.count("\nimport os\n") != 1 or "\nimport asyncio\n" in result:
        raise RuntimeError("Production asyncio import baseline mismatch")
    result = result.replace("\nimport os\n", "\nimport asyncio\nimport os\n", 1)
    if result.count(IMPORT_MARKER) != 1:
        raise RuntimeError("Production album helper insertion baseline mismatch")
    anchor = "from bot.utils.message_utils import safe_edit_or_send\n"
    if new.count(anchor) != 1 or new.count("SUPER_ADMIN_ID") < 1:
        raise RuntimeError("Local album helper marker mismatch")
    helpers = new.split(anchor, 1)[1].split("SUPER_ADMIN_ID", 1)[0]
    if "async def _send_room_photo_album_summary" not in helpers or "def _cancel_room_photo_album_summaries" not in helpers:
        raise RuntimeError("Local album helpers missing")
    result = result.replace(IMPORT_MARKER, anchor + helpers + "SUPER_ADMIN_ID", 1)
    ast.parse(result)
    return result


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
        temp = ADMIN + ".room-album.tmp"
        try:
            if run(client, "systemctl is-active booking-bot.service") != "active":
                raise RuntimeError("Bot inactive before album rollout")
            if run(client, "systemctl is-active booking-bot-relay89.service") != "active":
                raise RuntimeError("Private Bot API tunnel inactive")
            with sftp.open(ADMIN, "rb") as file:
                original = file.read()
            if b"\r\n" in original:
                raise RuntimeError("Unexpected production newline convention")
            amended = patch_album_source(original.decode(), Path("bot/handlers/admin.py").read_text(encoding="utf-8"))
            backup = ADMIN + ".bak.room-album." + str(int(time.time()))
            run(client, "cp -p " + shlex.quote(ADMIN) + " " + shlex.quote(backup))
            with sftp.open(temp, "wx") as file:
                file.write(amended.encode())
            sftp.chmod(temp, sftp.stat(ADMIN).st_mode & 0o777)
            sftp.posix_rename(temp, ADMIN)
            changed = True
            run(client, "cd /opt/booking_bot && ./venv/bin/python -m py_compile bot/handlers/admin.py")
            run(client, "systemctl restart booking-bot.service", timeout=45)
            for check in range(3):
                time.sleep(8)
                if run(client, "systemctl is-active booking-bot.service") != "active":
                    raise RuntimeError("Bot inactive after album patch")
                print("bot_active_check_" + str(check + 1) + "=yes")
            health_script = """import subprocess
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','2 minutes ago','--no-pager','-o','cat'],capture_output=True,text=True,timeout=8)
s=p.stdout
print('polling_healthy='+str('Run polling for bot' in s and 'TelegramNetworkError' not in s))
print('traceback_count='+str(s.count('Traceback (most recent call last):')))"""
            health = run(client, "/opt/booking_bot/venv/bin/python -c " + shlex.quote(health_script))
            print(health)
            if "polling_healthy=True" not in health or "traceback_count=0" not in health:
                raise RuntimeError("Bot polling health failed after album patch")
            with sftp.open(ADMIN, "rb") as file:
                deployed = file.read()
            if hashlib.sha256(deployed).hexdigest() != hashlib.sha256(amended.encode()).hexdigest():
                raise RuntimeError("Deployed album bytes differ")
            print("bot_room_album_fix_deployed=yes")
        except Exception:
            if changed and backup:
                try:
                    run(client, "cp -p " + shlex.quote(backup) + " " + shlex.quote(ADMIN)
                        + " && systemctl restart booking-bot.service", timeout=45)
                    print("bot_room_album_rollback=yes")
                except Exception:
                    print("bot_room_album_rollback_incomplete=yes")
            raise
        finally:
            try:
                sftp.remove(temp)
            except IOError:
                pass
            sftp.close()
    except Exception:
        raise RuntimeError("ServGo room-album deploy failed; details suppressed") from None
    finally:
        client.close()


if __name__ == "__main__":
    main()
