"""Scoped rollout: personal cabinet of a master (any login method) + employees.access_email.

Replaces five reviewed files and adds one new file on production, only when each existing file is
byte-identical to the reviewed `main` baseline (or already equals the new version). Anything else
stops the rollout. Order matters: the new nullable column is added FIRST (additive, harmless for the
old code), then files are replaced, py_compile + import check, restart of web API and bot, health
checks (the new /master/me route must answer 401 without a login), automatic rollback of files.
Without APPLY=true nothing on the server changes (read-only preflight).
The site pages go out through the regular "Deploy webapp to server" workflow on merge to main;
merge only AFTER this rollout, otherwise the employee form would offer a field the server ignores.
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

# sha256 of each file on `main` at fc6dfb4 (production state before this change).
BASELINE = {
    "database/database.py": "c0660e792d9db5ff8d5c74b7e722fce0819a8120905e3647d2329c097a436c58",
    "database/models.py": "151fdc8a10f3cd45db615d32866ccedc1840e3fc23c3d89939f8b2e3dd76ac15",
    "web/api/main.py": "e161caa65b11f927f76a029332c88b18240bae0a7449bf6ea545ae253cc976e5",
    "web/api/routes/admin.py": "894aa8ad55e9c39b98ac13d58fe893ae0a7b2ae8714c1e11e33c1d38476c0afa",
    "web/api/schemas.py": "4976b641c54a7a47101ce020e4eaf5faa1f0c68495d8dee26cc81501a087673f",
}
NEW_FILES = ["web/api/routes/master.py"]
MIGRATION = (
    "import asyncio\n"
    "from sqlalchemy import text\n"
    "from database.database import engine\n"
    "async def go():\n"
    "    async with engine.begin() as conn:\n"
    "        await conn.execute(text('ALTER TABLE employees ADD COLUMN IF NOT EXISTS access_email VARCHAR(320)'))\n"
    "        await conn.execute(text('CREATE INDEX IF NOT EXISTS ix_employees_access_email ON employees (access_email)'))\n"
    "        rows = (await conn.execute(text('SELECT count(*) FROM employees WHERE access_email IS NULL'))).scalar_one()\n"
    "    print('access_email_column=yes; employees_without_access_email=%s' % rows)\n"
    "asyncio.run(go())\n"
)
STAMP_TAG = ".bak.master."


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
            print("master_cabinet_already_deployed=yes; production_untouched=yes")
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
                    "web.api.routes.admin, database.models\"", 60)

        run(client, "systemctl restart " + WEB_SERVICE, timeout=60)
        wait_web_ready(client)
        since = server_now(client)
        run(client, "systemctl restart " + BOT_SERVICE, timeout=60)
        if not bot_polling_healthy(client, since):
            raise RuntimeError("Bot did not start polling cleanly")
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        code = run(client, "curl -sS --max-time 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/master/me || true")
        if code != "401":
            raise RuntimeError("New /master/me route did not answer 401 without a login (got %s)" % code)
        print("backups=" + ",".join(sorted(backups.values())))
        print("master_cabinet_deployed=yes; web_api_health=200; bot_polling=yes; master_me_without_login=401")
    except Exception:
        if backups or created:
            try:
                steps = ["cp -p %s %s" % (shlex.quote(b), shlex.quote(ROOT + "/" + p)) for p, b in backups.items()]
                steps += ["rm -f " + shlex.quote(path) for path in created]
                run(client, "set -e; " + "; ".join(steps) + "; systemctl restart " + WEB_SERVICE
                    + "; systemctl restart " + BOT_SERVICE, 90)
                print("master_cabinet_rolled_back=yes")
            except Exception:
                print("master_cabinet_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
