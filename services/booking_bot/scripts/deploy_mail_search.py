"""Scoped rollout of the ServGo Mail company search (PR #76).

The source audit of 23.10.2026 showed the production backend byte-identical to GitHub, so these seven files are
replaced whole - but only when the server copy is exactly the version this change was built on (EXPECTED_OLD).
Any other content on the server means somebody changed it since: the script refuses and touches nothing.

Order (nothing changes before every check passed):
  1. read-only: web API active and healthy, every server file is the expected old version (or already the new one),
     the new files compile; the processes that run mail code are found (names of settings are listed, never values);
  2. backups of the files and of the mail database;
  3. file replacement, compile and import check;
  4. restart booking-web-api.service and the unit that runs the mail worker (if it is a separate unit), health check,
     the new address answers (401/403 = the owner's login is required, 404 = broken);
  5. any failure after step 3 restores the files and restarts what was restarted.
     The two new campaign columns stay (harmless: the old code ignores them).

APPLY=true is required to change anything; otherwise this is a read-only preflight. Secrets are never printed.
"""
import base64
import hashlib
import logging
import os
import re
import shlex
import time
from pathlib import Path

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
WEB_UNIT = "booking-web-api.service"
HEALTH = "http://127.0.0.1:8000/health"
NEW_URL = "/owner-mail/api/company-search"

# server path (relative to ROOT) -> sha256 of the production file the change was built on; None = new file
EXPECTED_OLD = {
    "web/api/main.py": "67a270a3a281ffcb269433d741479870bd923c2406b961816f4bc242199bc72f",
    "web/api/routes/mail_campaign_api.py": "7d0f92c66ce79d479e89b6d5831bfd8f99b5a9f927fcdaabaf80a10a10308ede",
    "web/api/routes/mail_campaign_dashboard.html": "e68da89b9860f0d3e810f38b2b967fa73f26d8f7fd12ca858dbefb0241f2a301",
    "web/api/routes/mail_campaign_research.py": "72cb9618db33c54990d6e872f4145315c0fe470941f077a78fe8471c58884682",
    "web/api/routes/mail_campaign_store.py": "42fbf7a904ae2b558ec3fdab2ff7b58d638e8b5e3c36a9c9af8c6eb9d5ee0cce",
    "web/api/routes/owner_mail.py": "7a4396afc3a9cfd4787062c184fad684707fdd792334bcc12d1e65a32481888b",
    "web/api/routes/mail_company_search.py": None,
}
SETTINGS_TO_REPORT = ("CLAUDEHUB_API_KEY", "XAI_API_KEY", "STUDIO_INTERNAL_KEY", "SERVGO_MAIL_DATA_DIR",
                      "SERVGO_MAIL_AI_MODEL", "GROWTH_INTERNAL_URL")
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decide(server: bytes | None, old_sha: str | None, new: bytes) -> str:
    """'copy' (safe to replace), 'same' (already the new version) or 'refuse' (server holds something unexpected)."""
    if server is not None and sha(server) == sha(new):
        return "same"
    if server is None:
        return "copy" if old_sha is None else "refuse"
    return "copy" if old_sha is not None and sha(server) == old_sha else "refuse"


def units_from_cgroup(text: str) -> set[str]:
    """systemd service names found in the text of /proc/<pid>/cgroup."""
    return set(re.findall(r"([A-Za-z0-9@_.:\\-]+\.service)", text))


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def run(client, command: str, timeout: int = 60) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def put(sftp, path: str, data: bytes, mode: int = 0o644):
    temp = path + ".mail.tmp"
    try:
        sftp.remove(temp)
    except IOError:
        pass
    with sftp.open(temp, "wx") as out:
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


def read(sftp, path: str):
    try:
        with sftp.open(path, "rb") as handle:
            return handle.read()
    except IOError:
        return None


def code_of(client, url_path: str) -> str:
    return run(client, "curl -sS --max-time 5 -o /dev/null -w '%{http_code}' "
               + shlex.quote("http://127.0.0.1:8000" + url_path) + " || true")


def wait_health(client):
    for _ in range(20):
        if code_of(client, "/health") == "200":
            return
        time.sleep(2)
    raise RuntimeError("Web API did not become healthy")


DISCOVER = r"""
import os, pathlib, re
names = %r
found = {}
for pid in filter(str.isdigit, os.listdir('/proc')):
    try:
        cmd = pathlib.Path('/proc/%%s/cmdline' %% pid).read_bytes().replace(b'\0', b' ').decode('utf-8', 'replace')
    except OSError:
        continue
    if not (re.search(r'mail_campaign_worker|owner_mail|web\.api\.main', cmd) and ('python' in cmd.lower() or 'uvicorn' in cmd)):
        continue
    try:
        cg = pathlib.Path('/proc/%%s/cgroup' %% pid).read_text()
        env = pathlib.Path('/proc/%%s/environ' %% pid).read_bytes().split(b'\0')
    except OSError:
        continue
    keys = {e.split(b'=', 1)[0].decode() for e in env if b'=' in e}
    unit = ','.join(sorted(set(re.findall(r'([A-Za-z0-9@_.:\\-]+\.service)', cg)))) or 'no-systemd-unit'
    short = re.sub(r'\s+', ' ', cmd)[:90]
    print('proc|%%s|%%s|%%s' %% (unit, short, ','.join(n + '=' + ('set' if n in keys else 'missing') for n in names)))
"""


def discover(client):
    """Processes that run mail code: (unit, command, which settings exist). Values are never read out."""
    out = run(client, "python3 - <<'PY'\n" + DISCOVER % (SETTINGS_TO_REPORT,) + "\nPY")
    return [line.split("|", 3)[1:] for line in out.splitlines() if line.startswith("proc|")]


def connect():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    for attempt in range(3):
        candidate = paramiko.SSHClient()
        candidate.set_missing_host_key_policy(PinnedKey())
        try:
            candidate.connect(HOST, port=22, username="root", password=password, allow_agent=False,
                              look_for_keys=False, timeout=12, banner_timeout=25, auth_timeout=15)
            return candidate
        except Exception:
            candidate.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)


def main():
    apply = os.environ.get("APPLY", "false").lower() == "true"
    client = connect()
    sftp = client.open_sftp()
    stamp = str(int(time.time()))
    backups, created, restarted = {}, [], []
    units = set()
    changed = False
    try:
        if run(client, "systemctl is-active " + WEB_UNIT) != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        if code_of(client, "/health") != "200":
            raise RuntimeError("Web API health was not 200 before the change; refusing")

        # 1. read-only checks
        plan = {}
        for rel, old_sha in EXPECTED_OLD.items():
            new = Path(rel).read_bytes()
            if rel.endswith(".py"):
                compile(new.decode("utf-8"), rel, "exec")
            server = read(sftp, ROOT + "/" + rel)
            plan[rel] = (decide(server, old_sha, new), new, server is not None)
            print("%s: server=%s -> %s" % (rel, sha(server)[:12] if server is not None else "absent", plan[rel][0]))
        refused = [rel for rel, (action, _, _) in plan.items() if action == "refuse"]
        if refused:
            raise RuntimeError("Server files are not the expected versions, nothing changed: " + ", ".join(refused))
        procs = discover(client)
        for unit, cmd, settings in procs:
            print("process unit=%s cmd=%s settings=%s" % (unit, cmd, settings))
            units.update(u for u in unit.split(",") if u.endswith(".service"))
        print("before_company_search_address=" + code_of(client, NEW_URL))
        todo = [rel for rel, (action, _, _) in plan.items() if action == "copy"]
        print("files_to_copy=" + (",".join(todo) or "none") + " units=" + ",".join(sorted(units | {WEB_UNIT})))
        if not apply:
            print("read_only_preflight=yes; production_untouched=yes")
            return
        if not todo:
            print("nothing_to_do=yes")
            return

        # 2. backups: files, and the mail database (copy, kept)
        for rel in todo:
            path = ROOT + "/" + rel
            if plan[rel][2]:
                backups[path] = path + ".bak.mail." + stamp
                run(client, "cp -p %s %s" % (shlex.quote(path), shlex.quote(backups[path])))
            else:
                created.append(path)
        data_dir = run(client, "python3 -c \"import os;print(os.getenv('SERVGO_MAIL_DATA_DIR','/var/lib/servgo-owner-mail'))\"")
        run(client, "[ ! -f %s/mail.sqlite ] || cp -p %s/mail.sqlite %s/mail.sqlite.bak.mail.%s"
            % (shlex.quote(data_dir), shlex.quote(data_dir), shlex.quote(data_dir), stamp))

        # 3. replace, compile, import
        changed = True
        for rel in todo:
            path = ROOT + "/" + rel
            mode = sftp.stat(path).st_mode & 0o777 if plan[rel][2] else 0o644
            put(sftp, path, plan[rel][1], mode)
        py = " ".join(shlex.quote(rel) for rel in todo if rel.endswith(".py"))
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile " + py)
        imp = run(client, "cd " + ROOT + " && (./venv/bin/python -c 'import web.api.main' >/dev/null 2>&1 && echo yes || echo no)",
                  timeout=90)
        print("compile_ok=yes import_check=" + imp)

        # 4. restart and verify
        for unit in [WEB_UNIT] + sorted(units - {WEB_UNIT}):
            run(client, "systemctl restart " + shlex.quote(unit), timeout=60)
            restarted.append(unit)
        wait_health(client)
        for unit in restarted:
            if run(client, "systemctl is-active " + shlex.quote(unit)) != "active":
                raise RuntimeError("Unit failed after rollout: " + unit)
        after = code_of(client, NEW_URL)
        print("company_search_address_without_login=" + after)
        if after not in ("401", "403"):
            raise RuntimeError("New address does not answer as expected (401/403 = login required, 404 = not mounted)")
        print("backups=" + ",".join(sorted(os.path.basename(v) for v in backups.values())))
        print("mail_search_deployed=yes; web_api_health=200; restarted=" + ",".join(restarted))
    except Exception:
        if changed:
            try:
                steps = ["cp -p %s %s" % (shlex.quote(b), shlex.quote(p)) for p, b in backups.items()]
                steps += ["rm -f " + shlex.quote(p) for p in created]
                steps += ["systemctl restart " + shlex.quote(u) for u in sorted(units | {WEB_UNIT})]
                run(client, "set -e; " + "; ".join(steps), timeout=120)
                wait_health(client)
                print("mail_search_rolled_back=yes")
            except Exception:
                print("mail_search_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
