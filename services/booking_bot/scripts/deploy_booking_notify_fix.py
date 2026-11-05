"""Scoped rollout of the repeated «Новая подтверждённая запись» fix.

Production admin.py diverges from git, so the file is not replaced: only the
notification block is moved from list_bookings (GET, polled every 15 s by the
schedule) to create_manual_booking (POST). Pinned SSH, backup and rollback.
"""
import ast
import base64
import hashlib
import logging
import os
import shlex
import time

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
ADMIN = ROOT + "/web/api/routes/admin.py"
SERVICE = "booking-web-api.service"
FINAL_RETURN = "return [_admin_booking_out(b) for b in bookings]"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

NOTIFY_BLOCK = '''
    try:
        from bot.services.notification_events import dispatch_booking_event
        # Recurring manual bookings are distinct visits, so notify each one
        # independently rather than presenting the whole series as one visit.
        for created in bookings:
            await dispatch_booking_event(
                [created],
                db,
                "booking_created",
                include_client=True,
                staff_audience="all",
            )
    except Exception as notification_error:
        logger.error(
            "Manual booking notification failed ids=%s: %s",
            [item.id for item in bookings],
            notification_error,
            exc_info=True,
        )

'''


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def _function(tree, name):
    found = [x for x in ast.walk(tree) if isinstance(x, ast.AsyncFunctionDef) and x.name == name]
    if len(found) != 1:
        raise RuntimeError("Unexpected production structure: " + name)
    return found[0]


def _notify_tries(lines, func):
    return [node for node in func.body if isinstance(node, ast.Try)
            and "dispatch_booking_event" in "".join(lines[node.lineno - 1:node.end_lineno])]


def patch_admin_source(source: str):
    """Return (patched source, changed). Touches only the two booking handlers."""
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    if "\nlogger = " not in source:
        raise RuntimeError("Unexpected production structure: logger")
    listing = _function(tree, "list_bookings")
    manual = _function(tree, "create_manual_booking")
    list_tries = _notify_tries(lines, listing)
    manual_tries = _notify_tries(lines, manual)
    if not list_tries and len(manual_tries) == 1:
        return source, False
    if len(list_tries) != 1 or manual_tries:
        raise RuntimeError("Production booking notifications differ from the reviewed bug")
    last = manual.body[-1]
    if not isinstance(last, ast.Return) or lines[last.lineno - 1].strip() != FINAL_RETURN:
        raise RuntimeError("Unexpected create_manual_booking ending")
    if listing.end_lineno >= manual.lineno:
        raise RuntimeError("Unexpected handler order")

    # Edit the later function first so earlier line numbers stay valid.
    start = last.lineno - 1
    lines[start:start] = NOTIFY_BLOCK.splitlines(keepends=True)
    remove = list_tries[0]
    begin, end = remove.lineno - 1, remove.end_lineno
    if begin > 0 and not lines[begin - 1].strip():
        begin -= 1
    if end < len(lines) and not lines[end].strip():
        end += 1
    del lines[begin:end]
    result = "".join(lines)

    check = ast.parse(result)
    patched_lines = result.splitlines(keepends=True)
    if _notify_tries(patched_lines, _function(check, "list_bookings")):
        raise RuntimeError("Patch left notifications in list_bookings")
    if len(_notify_tries(patched_lines, _function(check, "create_manual_booking"))) != 1:
        raise RuntimeError("Patch did not add manual booking notification")
    return result, True


def run(client, command: str, timeout: int = 45) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def put(sftp, path: str, data: bytes, mode: int = 0o644):
    temp = path + ".notify-fix.tmp"
    with sftp.open(temp, "wx") as out:
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    client = None
    for attempt in range(3):
        candidate = paramiko.SSHClient()
        candidate.set_missing_host_key_policy(PinnedKey())
        try:
            candidate.connect(HOST, port=22, username="root", password=password,
                              allow_agent=False, look_for_keys=False, timeout=12,
                              banner_timeout=25, auth_timeout=15)
            client = candidate
            break
        except Exception:
            candidate.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)
    sftp = client.open_sftp()
    changed = False
    backup = ADMIN + ".bak.notify." + str(int(time.time()))
    try:
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        with sftp.open(ADMIN, "rb") as source:
            original = source.read()
        print("production_admin_sha256_before=" + hashlib.sha256(original).hexdigest())
        patched, needed = patch_admin_source(original.decode("utf-8"))
        if not needed:
            print("notify_fix_already_present=yes; production_untouched=yes")
            return
        run(client, "cp -p " + shlex.quote(ADMIN) + " " + shlex.quote(backup))
        changed = True
        put(sftp, ADMIN, patched.encode("utf-8"), sftp.stat(ADMIN).st_mode & 0o777)
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile web/api/routes/admin.py")
        run(client, "systemctl restart " + SERVICE, timeout=45)
        for attempt in range(15):
            if run(client, "curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true") == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Web API did not become ready after rollout")
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        print("backup=" + backup)
        print("booking_notify_fix_deployed=yes; web_api_health=200")
    except Exception:
        if changed:
            try:
                run(client, "set -e; cp -p " + shlex.quote(backup) + " " + shlex.quote(ADMIN)
                    + "; rm -f " + shlex.quote(ADMIN + ".notify-fix.tmp")
                    + "; systemctl restart " + SERVICE, timeout=60)
                print("booking_notify_fix_rolled_back=yes")
            except Exception:
                print("booking_notify_fix_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
