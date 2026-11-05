"""Manually deploy a guarded, bot-only late-album-notification fix."""
import ast
import hashlib
import logging
import os
from pathlib import Path
import shlex
import time

import paramiko

from scripts.deploy_bot_room_photo_fix import ADMIN, HOST, PinnedKey, nodes, run

PRODUCTION_SHA256 = "26969f5e7f3714335f01863d2fec60fff78797a91f54ab9acfa42b98bb07f4f4"
BASELINE = {
    "edit_emp_photos_menu": "930ed6d7a42bac325252cb0adf6f22f1fa929fbdbd9afd3b11575c61830cf9ee",
    "emp_add_photo_start": "f8c36a018544597eac912e7c9ad4a08b1bd260bacfb59b534b10e7f961d10e98",
    "emp_add_photo_receive": "c3d1cc6b262b6e5018c82e14e6f93a3946a2c820881249f06aef406f408d0752",
    "emp_photos_done": "cecd52620464a5856339ceb385065515b7c81fc5b012cae366c3416124cc2ed6",
    "emp_photos_back": "b9645e9dc8773301dae60dbe1cfd7c723408eed1b32614e0ea7338cf7d210745",
}
PENDING_MARKER = "_room_photo_album_pending = {}\n\n\nasync def _send_room_photo_album_summary"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


def patch_session_source(original, updated, expected_sha=PRODUCTION_SHA256, baseline=BASELINE):
    if hashlib.sha256(original.encode()).hexdigest() != expected_sha:
        raise RuntimeError("Live admin.py SHA mismatch; no changes applied")
    old_nodes, new_nodes = nodes(original), nodes(updated)
    lines = original.splitlines(keepends=True)
    for name, expected in sorted(baseline.items(), key=lambda item: old_nodes[item[0]].lineno, reverse=True):
        old = old_nodes[name]
        if hashlib.sha256(ast.get_source_segment(original, old).encode()).hexdigest() != expected:
            raise RuntimeError("Live photo function mismatch: " + name)
        lines[old.lineno - 1:old.end_lineno] = [ast.get_source_segment(updated, new_nodes[name]) + "\n"]
    result = "".join(lines)
    if result.count(PENDING_MARKER) != 1 or updated.count("_room_photo_album_pending = {}\n") != 1:
        raise RuntimeError("Live album helper anchor mismatch")
    capture_helpers = updated.split("_room_photo_album_pending = {}\n", 1)[1].split(
        "async def _send_room_photo_album_summary", 1)[0]
    if "def _advance_room_photo_capture" not in capture_helpers or "_room_photo_capture_sessions = {}" not in capture_helpers:
        raise RuntimeError("Local capture helper is missing")
    result = result.replace(PENDING_MARKER, "_room_photo_album_pending = {}\n" + capture_helpers
                            + "async def _send_room_photo_album_summary", 1)
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
        temp = ADMIN + ".room-album-session.tmp"
        try:
            if run(client, "systemctl is-active booking-bot.service") != "active":
                raise RuntimeError("Bot inactive before rollout")
            if run(client, "systemctl is-active booking-bot-relay89.service") != "active":
                raise RuntimeError("Private Bot API tunnel inactive")
            with sftp.open(ADMIN, "rb") as file:
                original = file.read()
            if b"\r\n" in original:
                raise RuntimeError("Unexpected production newline convention")
            amended = patch_session_source(original.decode("utf-8"),
                                           Path("bot/handlers/admin.py").read_text(encoding="utf-8"))
            backup = ADMIN + ".bak.room-album-session." + str(int(time.time()))
            run(client, "cp -p " + shlex.quote(ADMIN) + " " + shlex.quote(backup))
            with sftp.open(temp, "wx") as file:
                file.write(amended.encode("utf-8"))
            sftp.chmod(temp, sftp.stat(ADMIN).st_mode & 0o777)
            sftp.posix_rename(temp, ADMIN)
            changed = True
            run(client, "cd /opt/booking_bot && ./venv/bin/python -m py_compile bot/handlers/admin.py")
            run(client, "systemctl restart booking-bot.service", timeout=45)
            for check in range(3):
                time.sleep(8)
                if run(client, "systemctl is-active booking-bot.service") != "active":
                    raise RuntimeError("Bot inactive after session patch")
                print("bot_active_check_" + str(check + 1) + "=yes")
            health_script = """import subprocess
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','2 minutes ago','--no-pager','-o','cat'],capture_output=True,text=True,timeout=8)
s=p.stdout
print('polling_healthy='+str('Run polling for bot' in s and 'TelegramNetworkError' not in s))
print('traceback_count='+str(s.count('Traceback (most recent call last):')))"""
            health = run(client, "/opt/booking_bot/venv/bin/python -c " + shlex.quote(health_script))
            print(health)
            if "polling_healthy=True" not in health or "traceback_count=0" not in health:
                raise RuntimeError("Bot polling health failed")
            with sftp.open(ADMIN, "rb") as file:
                deployed = file.read()
            if hashlib.sha256(deployed).hexdigest() != hashlib.sha256(amended.encode()).hexdigest():
                raise RuntimeError("Deployed bytes differ")
            print("bot_room_album_session_fix_deployed=yes")
        except Exception:
            if changed and backup:
                try:
                    run(client, "cp -p " + shlex.quote(backup) + " " + shlex.quote(ADMIN)
                        + " && systemctl restart booking-bot.service", timeout=45)
                    print("bot_room_album_session_rollback=yes")
                except Exception:
                    print("bot_room_album_session_rollback_incomplete=yes")
            raise
        finally:
            try:
                sftp.remove(temp)
            except IOError:
                pass
            sftp.close()
    except Exception:
        raise RuntimeError("ServGo album-session deploy failed; details suppressed") from None
    finally:
        client.close()


if __name__ == "__main__":
    main()
