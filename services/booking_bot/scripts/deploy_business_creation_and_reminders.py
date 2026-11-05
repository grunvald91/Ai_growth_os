"""Scoped rollout: business creation (bot + web admin) and the reminder-checker fix.

Replaces exactly four files on production, and only when each one is byte-identical to the
reviewed `main` baseline (or already equals the new version). Anything else stops the rollout.
Pinned SSH host key, timestamped backups, py_compile, restart of both services, health checks,
automatic rollback. Without APPLY=true nothing on the server changes (read-only preflight).
The web frontend is deployed by the regular "Deploy webapp to server" workflow on merge to main.
"""
import base64
import hashlib
import logging
import os
import shlex
import time
from pathlib import Path

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
WEB_SERVICE = "booking-web-api.service"
BOT_SERVICE = "booking-bot.service"

# sha256 of each file on `main` at e91df5c (production was verified identical by the audit of 2026-10-25).
BASELINE = {
    "bot/handlers/admin.py": "f786d2a624d3ee557202f544636f28f83d547bc61ee7a38b5fe8ff92ea424f67",
    "bot/services/notifications.py": "b8158221beb917f582bad4cd3e9ecce3319f040986420cccd19eab60b0c1bb10",
    "web/api/routes/admin.py": "720415b49f93118cbf403436b4b75467e86330ea66961b11375e898ec6c7b8e2",
    "web/api/schemas.py": "bef4f39faaa38b6d7aca33e716a3877be39448a743cdd5fb32b03497d425aa82",
}
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(server_sha: str, baseline_sha: str, new_sha: str) -> str:
    """'skip' = already the new version, 'deploy' = still the reviewed baseline, otherwise refuse."""
    if server_sha == new_sha:
        return "skip"
    if server_sha == baseline_sha:
        return "deploy"
    raise RuntimeError("Production file differs from the reviewed baseline; refusing to overwrite it")


def run(client, command: str, timeout: int = 45) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def put(sftp, path: str, data: bytes, mode: int):
    temp = path + ".bizrem.tmp"
    try:
        sftp.remove(temp)
    except OSError:
        pass
    with sftp.open(temp, "wx") as out:
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


def connect(password: str):
    for attempt in range(3):
        candidate = paramiko.SSHClient()
        candidate.set_missing_host_key_policy(PinnedKey())
        try:
            candidate.connect(HOST, port=22, username="root", password=password,
                              allow_agent=False, look_for_keys=False, timeout=12,
                              banner_timeout=25, auth_timeout=15)
            return candidate
        except Exception:
            candidate.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)


def wait_web_ready(client):
    for _ in range(20):
        code = run(client, "curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true")
        if code == "200":
            return
        time.sleep(2)
    raise RuntimeError("Web API did not become ready")


def server_now(client) -> str:
    return run(client, "date '+%Y-%m-%d %H:%M:%S'")


def bot_polling_healthy(client, since: str) -> bool:
    """Look only at log lines written after `since` (server time taken right before the restart)."""
    for _ in range(20):
        if run(client, "systemctl is-active " + BOT_SERVICE) != "active":
            raise RuntimeError("Bot service is not active after restart")
        log = run(client, "journalctl -u " + BOT_SERVICE + " --since " + shlex.quote(since) + " --no-pager -o cat || true", 15)
        if "Run polling for bot" in log:
            return "TelegramNetworkError" not in log
        time.sleep(3)
    return False


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    apply = os.environ.get("APPLY", "false").lower() == "true"
    repo = Path(__file__).resolve().parent.parent
    new = {path: (repo / path).read_bytes() for path in BASELINE}

    client = connect(password)
    sftp = client.open_sftp()
    backups = {}
    try:
        for service in (WEB_SERVICE, BOT_SERVICE):
            if run(client, "systemctl is-active " + service) != "active":
                raise RuntimeError(service + " was inactive; refusing change")
        # Environment the new web-admin endpoint relies on (shared .env of the bot); booleans only.
        env_check = run(client, "cd " + ROOT + " && ./venv/bin/python -c \"import bot.config as c;"
                        "print('bot_token_set=%s mlm_password_set=%s' % (bool(c.BOT_TOKEN), bool(c.MLM_DB_PASSWORD)))\"")
        print(env_check)

        plan = {}
        for path, baseline in BASELINE.items():
            with sftp.open(ROOT + "/" + path, "rb") as handle:
                current = handle.read()
            plan[path] = classify(sha(current), baseline, sha(new[path]))
            print("%s: %s" % (path, plan[path]))
        todo = [path for path, action in plan.items() if action == "deploy"]
        if not todo:
            print("business_reminders_already_deployed=yes; production_untouched=yes")
            return
        if not apply:
            print("preflight_ok=yes; would_deploy=%d; production_untouched=yes" % len(todo))
            return

        stamp = str(int(time.time()))
        for path in todo:
            target = ROOT + "/" + path
            backup = target + ".bak.bizrem." + stamp
            run(client, "cp -p " + shlex.quote(target) + " " + shlex.quote(backup))
            backups[path] = backup  # only once the copy exists, so a rollback never restores a missing file
        for path in todo:
            target = ROOT + "/" + path
            put(sftp, target, new[path], sftp.stat(target).st_mode & 0o777)
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile " + " ".join(shlex.quote(p) for p in todo))
        run(client, "cd " + ROOT + " && ./venv/bin/python -c \"import web.api.routes.admin, bot.handlers.admin, "
                    "bot.services.notifications\"", 60)

        run(client, "systemctl restart " + WEB_SERVICE, timeout=60)
        wait_web_ready(client)
        since = server_now(client)
        run(client, "systemctl restart " + BOT_SERVICE, timeout=60)
        if not bot_polling_healthy(client, since):
            raise RuntimeError("Bot did not start polling cleanly")
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        print("backups=" + ",".join(sorted(backups.values())))
        print("business_reminders_deployed=yes; web_api_health=200; bot_polling=yes")
    except Exception:
        if backups:
            try:
                restore = "set -e; " + "; ".join(
                    "cp -p %s %s" % (shlex.quote(b), shlex.quote(ROOT + "/" + p)) for p, b in backups.items())
                run(client, restore + "; systemctl restart " + WEB_SERVICE + "; systemctl restart " + BOT_SERVICE, 90)
                print("business_reminders_rolled_back=yes")
            except Exception:
                print("business_reminders_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
