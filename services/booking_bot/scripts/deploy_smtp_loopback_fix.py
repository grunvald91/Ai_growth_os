"""Scoped rollout of the local-mail-server fix (bot/services/email_notifications.py only).

Same safety model as deploy_business_creation_and_reminders: the file is replaced only if production still
holds the reviewed baseline; backup, py_compile, restart of web API and bot, health checks, automatic
rollback. Without APPLY=true nothing changes. Afterwards a dry SMTP session against the local mail server
(EHLO, MAIL FROM, RCPT TO, RSET, QUIT - no message is sent) shows that mail is accepted.
"""
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.deploy_business_creation_and_reminders import (  # noqa: E402
    BOT_SERVICE, ROOT, WEB_SERVICE, bot_polling_healthy, classify, connect, put, run, server_now, sha,
    wait_web_ready,
)

PATH = "bot/services/email_notifications.py"
BASELINE = "361042f6acf4fdc67dc5840f2cb69820d94add55cda9014d72a359a2ba8321e1"

DRY_SESSION = r'''
import os, smtplib, sys
sys.path.insert(0, "/opt/booking_bot"); os.chdir("/opt/booking_bot")
import bot.config
host = os.getenv("SMTP_HOST", ""); port = int(os.getenv("SMTP_PORT", "587"))
sender = os.getenv("SMTP_FROM_EMAIL", "")
try:
    s = smtplib.SMTP(host, port, timeout=12); s.ehlo()
    code, _ = s.mail(sender); print("MAIL_FROM", code)
    code, _ = s.rcpt("smtp-check@example.org"); print("RCPT_EXTERNAL", code)
    s.rset(); s.quit()
except Exception as exc:
    print("SMTP_SESSION_ERROR", type(exc).__name__, str(exc)[:160])
'''


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    apply = os.environ.get("APPLY", "false").lower() == "true"
    new = (Path(__file__).resolve().parent.parent / PATH).read_bytes()
    client = connect(password)
    sftp = client.open_sftp()
    target = ROOT + "/" + PATH
    backup = None
    try:
        for service in (WEB_SERVICE, BOT_SERVICE):
            if run(client, "systemctl is-active " + service) != "active":
                raise RuntimeError(service + " was inactive; refusing change")
        with sftp.open(target, "rb") as handle:
            current = handle.read()
        action = classify(sha(current), BASELINE, sha(new))
        print(PATH + ": " + action)
        if action == "skip":
            print("smtp_loopback_already_deployed=yes; production_untouched=yes")
        elif not apply:
            print("preflight_ok=yes; would_deploy=1; production_untouched=yes")
        else:
            copy = target + ".bak.smtp." + str(int(time.time()))
            run(client, "cp -p " + shlex.quote(target) + " " + shlex.quote(copy))
            backup = copy  # only once the copy exists, so a rollback never restores a missing file
            put(sftp, target, new, sftp.stat(target).st_mode & 0o777)
            run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile " + PATH)
            run(client, "cd " + ROOT + " && ./venv/bin/python -c 'import bot.services.email_notifications'", 60)
            run(client, "systemctl restart " + WEB_SERVICE, timeout=60)
            wait_web_ready(client)
            since = server_now(client)
            run(client, "systemctl restart " + BOT_SERVICE, timeout=60)
            if not bot_polling_healthy(client, since):
                raise RuntimeError("Bot did not start polling cleanly")
            print("backup=" + backup)
            print("smtp_loopback_deployed=yes; web_api_health=200; bot_polling=yes")
        print(run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(DRY_SESSION), 40))
    except Exception:
        if backup:
            try:
                run(client, "set -e; cp -p %s %s; systemctl restart %s; systemctl restart %s" % (
                    shlex.quote(backup), shlex.quote(target), WEB_SERVICE, BOT_SERVICE), 90)
                print("smtp_loopback_rolled_back=yes")
            except Exception:
                print("smtp_loopback_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
