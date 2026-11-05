"""Scoped rollout: change a booking's service + "finish now" in the schedule (owner/admin/master, web + bot),
plus chat wording without "ИИ".

Replaces twelve reviewed files and adds one new file on production, only when each existing file is
byte-identical to the reviewed `main` baseline (or already equals the new version). Anything else
stops the rollout. Order matters: the new nullable column bookings.finished_at is added FIRST (additive,
harmless for the old code), then files are replaced, py_compile + import check, restart of web API and bot,
health checks (the new master route must answer 401 without a login), automatic rollback of files.
Without APPLY=true nothing on the server changes (read-only preflight).
The site pages go out through the regular "Deploy webapp to server" workflow on merge to main;
merge only AFTER this rollout, otherwise the schedule would offer buttons the server does not have yet.
"""
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.deploy_business_creation_and_reminders import (  # noqa: E402
    ROOT, WEB_SERVICE, BOT_SERVICE, classify, connect, put, run, sha, server_now,
    wait_web_ready, bot_polling_healthy,
)

# sha256 of each file on `main` at 3c26157 (production state before this change).
BASELINE = {
    "database/database.py": "4f350a2fe638339883cfd6628629c6e0dcbf6a1de15d9ef2400ed42f1265c264",
    "database/models.py": "02e2c51df9637925938c3d7803f565532de4602ba5d85918c2021d0b2ff20e14",
    "web/api/routes/admin.py": "be6f09b7476003d9d009fa1132ec1aa06e1a4bedd2de4101d698c4219a6451e8",
    "web/api/routes/master.py": "cd646686dda85e9dcad27aee44f46e6dc3bf905ec4b7cbe9ec5fb0c0b7686b02",
    "web/api/schemas.py": "deb0eb5ea98654cd4f5c8d08dcaadaf1f10802f7e3737121344b08b909b5ea19",
    "web/api/sales/engine.py": "8dfb6cc0b9a97276f0315578f6a36f5ee5e924174f0430e505ecaf60c2b0a013",
    "web/api/sales/policy.py": "fb50ec6fe475087be84880160fafc76ee0c8aec3ceb27e4f0cc98f3dfc9b6f01",
    "web/api/sales/provider.py": "7486697549d1cc39f5df814b636968b711bd10f40ead923f614ab5a721a2d108",
    "web/api/sales/stages.py": "a062ff00a6a5a74b62050a5cf0599a33025cbf24a9b04ec98c7353a62963102d",
    "bot/handlers/admin.py": "7a37d37673ff7843e16f3a9e08c7bf0b16027a1d9cad3f0d8101a6d020391f3c",
    "bot/handlers/master.py": "69a442d3b79c83291939d16e6c1b65db7ebb8b79773381199c44b5fc19057d83",
    "bot/keyboards/master_kb.py": "8f9cd8b985b92fb0c341ede62a91bbcacf82164aacfff054b03bcfda2dd8f693",
}
NEW_FILES = ["database/booking_changes.py"]
MIGRATION = (
    "import asyncio\n"
    "from sqlalchemy import text\n"
    "from database.database import engine\n"
    "async def go():\n"
    "    async with engine.begin() as conn:\n"
    "        await conn.execute(text('ALTER TABLE bookings ADD COLUMN IF NOT EXISTS finished_at TIMESTAMP'))\n"
    "        rows = (await conn.execute(text('SELECT count(*) FROM bookings WHERE finished_at IS NOT NULL'))).scalar_one()\n"
    "    print('finished_at_column=yes; bookings_with_finished_at=%s' % rows)\n"
    "asyncio.run(go())\n"
)
STAMP_TAG = ".bak.schedule."


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    apply = os.environ.get("APPLY", "false").lower() == "true"
    repo = Path(__file__).resolve().parent.parent
    new = {path: (repo / path).read_bytes() for path in list(BASELINE) + NEW_FILES}

    client = connect(password)
    sftp = client.open_sftp()
    backups, created = {}, []
    try:
        for service in (WEB_SERVICE, BOT_SERVICE):
            if run(client, "systemctl is-active " + service) != "active":
                raise RuntimeError(service + " was inactive; refusing change")

        plan = {}
        for path, baseline in BASELINE.items():
            with sftp.open(ROOT + "/" + path, "rb") as handle:
                current = handle.read()
            plan[path] = classify(sha(current), baseline, sha(new[path]))
        for path in NEW_FILES:
            try:
                with sftp.open(ROOT + "/" + path, "rb") as handle:
                    existing = handle.read()
                plan[path] = "skip" if sha(existing) == sha(new[path]) else "conflict"
            except OSError:
                plan[path] = "create"
            if plan[path] == "conflict":
                raise RuntimeError(path + " already exists on production with other content; refusing")
        for path, action in plan.items():
            print("%s: %s" % (path, action))
        todo = [path for path, action in plan.items() if action in ("deploy", "create")]
        if not todo:
            print("schedule_changes_already_deployed=yes; production_untouched=yes")
            return
        if not apply:
            print("preflight_ok=yes; would_deploy=%d; production_untouched=yes" % len(todo))
            return

        # 1. additive column first: the old code ignores it, the new code needs it
        print(run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(MIGRATION), 60))

        stamp = str(int(time.time()))
        for path in todo:
            if plan[path] == "deploy":
                target = ROOT + "/" + path
                backup = target + STAMP_TAG + stamp
                run(client, "cp -p " + shlex.quote(target) + " " + shlex.quote(backup))
                backups[path] = backup
        for path in todo:
            target = ROOT + "/" + path
            if plan[path] == "deploy":
                put(sftp, target, new[path], sftp.stat(target).st_mode & 0o777)
            else:
                put(sftp, target, new[path], 0o644)
                created.append(target)
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile " + " ".join(shlex.quote(p) for p in todo))
        run(client, "cd " + ROOT + " && ./venv/bin/python -c \"import web.api.main, web.api.routes.master, "
                    "web.api.routes.admin, database.models, database.booking_changes, bot.handlers.master, bot.handlers.admin\"", 60)

        run(client, "systemctl restart " + WEB_SERVICE, timeout=60)
        wait_web_ready(client)
        since = server_now(client)
        run(client, "systemctl restart " + BOT_SERVICE, timeout=60)
        if not bot_polling_healthy(client, since):
            raise RuntimeError("Bot did not start polling cleanly")
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        code = run(client, "curl -sS -X POST --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/master/bookings/0/finish || true")
        if code != "401":
            raise RuntimeError("New master finish route did not answer 401 without a login (got %s)" % code)
        print("backups=" + ",".join(sorted(backups.values())))
        print("schedule_changes_deployed=yes; web_api_health=200; bot_polling=yes; master_finish_without_login=401")
    except Exception:
        if backups or created:
            try:
                steps = ["cp -p %s %s" % (shlex.quote(b), shlex.quote(ROOT + "/" + p)) for p, b in backups.items()]
                steps += ["rm -f " + shlex.quote(path) for path in created]
                run(client, "set -e; " + "; ".join(steps) + "; systemctl restart " + WEB_SERVICE
                    + "; systemctl restart " + BOT_SERVICE, 90)
                print("schedule_changes_rolled_back=yes")
            except Exception:
                print("schedule_changes_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
