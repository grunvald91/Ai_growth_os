"""Scoped rollout of the marketing-studio sign-in and schedule methods (PR #66, #67).

Production database/models.py, web/api/schemas.py and web/api/main.py differ from git (source audit of
23.10.2026: changed=3 of the touched files), so they are NOT replaced. Only the lines this feature needs are
added, found by their place in the syntax tree. Two new route files are copied as they are.

Order (nothing is changed before every check passed):
  1. read-only checks: service active, patches apply and compile, sign-in secrets prepared by the studio deploy;
  2. SQL migration (adds one column, off by default; safe to run twice);
  3. backups, file changes, compile + import check;
  4. systemd drop-in: the web API reads the two secrets from the studio's root-only handoff file;
  5. restart booking-web-api.service, health check, the new addresses answer (403 = secrets are set, 404 = broken);
  6. any failure after step 2 restores the files, removes the drop-in and restarts the service.
     The column stays (harmless, default off); to drop it by hand:
     ALTER TABLE businesses DROP COLUMN IF EXISTS studio_admin_access;

Secrets are never printed. APPLY=true is required to change anything; otherwise this is a read-only preflight.
"""
import ast
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
SERVICE = "booking-web-api.service"
MODELS = ROOT + "/database/models.py"
SCHEMAS = ROOT + "/web/api/schemas.py"
MAIN = ROOT + "/web/api/main.py"
NEW_ROUTES = {
    ROOT + "/web/api/routes/studio_sso.py": "web/api/routes/studio_sso.py",
    ROOT + "/web/api/routes/studio_schedule.py": "web/api/routes/studio_schedule.py",
}
HANDOFF = "/opt/ai_growth_os/deploy/servgo_sso.env"
HANDOFF_KEYS = ("STUDIO_SSO_SECRET", "STUDIO_INTERNAL_KEY")
DROPIN_DIR = "/etc/systemd/system/" + SERVICE + ".d"
DROPIN = DROPIN_DIR + "/studio-sso.conf"
DROPIN_TEXT = "[Service]\nEnvironmentFile=-" + HANDOFF + "\n"
MIGRATION = "migrations/add_studio_admin_access.sql"
HEALTH = "http://127.0.0.1:8000/health"
MODEL_COMMENT = "# Владелец разрешает администраторам заведения входить в маркетинговую студию (growth.servgo.ru)."
MODEL_LINE = 'studio_admin_access: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")'
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------- patches (pure functions, unit tested)

def _class(tree, name):
    found = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name]
    if len(found) != 1:
        raise RuntimeError("Unexpected production structure: class " + name)
    return found[0]


def _insert_after(lines, node, text):
    indent = " " * node.col_offset
    lines[node.end_lineno:node.end_lineno] = [indent + part + "\n" for part in text.split("\n")]


def _last_field(cls, annotated: bool):
    """The last plain field of a class body: `name: T = ...` (pydantic) or `name: Mapped[...] = ...` (ORM)."""
    fields = [n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
              and n.target.id != "model_config"]
    if not fields:
        raise RuntimeError("Unexpected production structure: no fields in " + cls.name)
    return fields[-1]


def _has_field(cls, name):
    return any(isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == name
               for n in cls.body)


def patch_models(source: str):
    """Add Business.studio_admin_access. Returns (source, changed)."""
    tree = ast.parse(source)
    business = _class(tree, "Business")
    if _has_field(business, "studio_admin_access"):
        return source, False
    anchors = [n for n in business.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
               and n.target.id == "legal_documents_enabled"]
    anchor = anchors[0] if len(anchors) == 1 else _last_field(business, True)
    # A multi-line anchor is fine: the new line goes after its last line.
    lines = source.splitlines(keepends=True)
    _insert_after(lines, anchor, MODEL_COMMENT + "\n" + MODEL_LINE)
    result = "".join(lines)
    if not _has_field(_class(ast.parse(result), "Business"), "studio_admin_access"):
        raise RuntimeError("Patch did not add the Business field")
    return result, True


def patch_schemas(source: str):
    """Add the switch to the settings input and to the admin detail output. Returns (source, changed)."""
    tree = ast.parse(source)
    wanted = (("BusinessUpdateIn", "studio_admin_access: Optional[bool] = None"),
              ("BusinessAdminDetailOut", "studio_admin_access: bool = False"))
    todo = []
    for name, text in wanted:
        cls = _class(tree, name)
        if not _has_field(cls, "studio_admin_access"):
            same = [n for n in cls.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                    and n.target.id == "allow_multiple_services"]
            todo.append((same[0] if same else _last_field(cls, False), text))
    if not todo:
        return source, False
    lines = source.splitlines(keepends=True)
    for anchor, text in sorted(todo, key=lambda item: -item[0].end_lineno):  # later class first
        _insert_after(lines, anchor, text)
    result = "".join(lines)
    check = ast.parse(result)
    for name, _ in wanted:
        if not _has_field(_class(check, name), "studio_admin_access"):
            raise RuntimeError("Patch did not add the schema field to " + name)
    return result, True


def _is_include(node, router_name):
    call = node.value if isinstance(node, ast.Expr) else None
    return (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "include_router"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "app" and call.args
            and isinstance(call.args[0], ast.Attribute) and isinstance(call.args[0].value, ast.Name)
            and call.args[0].value.id == router_name and call.args[0].attr == "router")


def patch_main(source: str):
    """Import the two new route modules and include their routers. Returns (source, changed)."""
    tree = ast.parse(source)
    names = ("studio_sso", "studio_schedule")
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.level == 1 and n.module == "routes"
               and any(a.name == "client" for a in n.names)]
    if len(imports) != 1 or imports[0].lineno != imports[0].end_lineno:
        raise RuntimeError("Unexpected production structure: routes import")
    imp = imports[0]
    have = {a.name for a in imp.names}
    includes = [n for n in tree.body if _is_include(n, "studio_sso") or _is_include(n, "studio_schedule")]
    if set(names) <= have and len(includes) == 2:
        return source, False
    if set(names) & have or includes:
        raise RuntimeError("Production main.py is partly patched; refusing to guess")
    anchors = [n for n in tree.body if _is_include(n, "admin")]
    if len(anchors) != 1:
        raise RuntimeError("Unexpected production structure: admin router include")
    lines = source.splitlines(keepends=True)
    anchor = anchors[0]
    lines[anchor.end_lineno:anchor.end_lineno] = [
        'app.include_router(studio_sso.router, tags=["studio"])\n',
        'app.include_router(studio_schedule.router, tags=["studio"])\n']
    original = lines[imp.lineno - 1]
    if not original.rstrip().endswith(tuple(have)):
        raise RuntimeError("Unexpected production structure: routes import line")
    lines[imp.lineno - 1] = original.rstrip("\n") + ", studio_sso, studio_schedule\n"
    result = "".join(lines)
    check = ast.parse(result)
    if sum(1 for n in check.body if _is_include(n, "studio_sso") or _is_include(n, "studio_schedule")) != 2:
        raise RuntimeError("Patch did not add the routers")
    return result, True


# ---------------------------------------------------------------- server plumbing

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
    temp = path + ".studio.tmp"
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
    return run(client, "curl -sS --max-time 5 -o /dev/null -w '%{http_code}' " + shlex.quote("http://127.0.0.1:8000" + url_path)
               + " || true")


def wait_health(client):
    for _ in range(20):
        if code_of(client, "/health") == "200":
            return
        time.sleep(2)
    raise RuntimeError("Web API did not become healthy")


def handoff_ready(client) -> bool:
    """Both secrets exist in the studio's handoff file and are long enough. Values are never read out."""
    check = ("python3 - <<'PY'\nimport pathlib\ntry:\n"
             " rows=dict(l.split('=',1) for l in pathlib.Path(%r).read_text().splitlines() if '=' in l)\n"
             " print('yes' if all(len(rows.get(k,'').strip())>=32 for k in %r) else 'no')\n"
             "except Exception:\n print('no')\nPY" % (HANDOFF, HANDOFF_KEYS))
    return run(client, check) == "yes"


def import_ok(client) -> bool:
    return run(client, "cd " + ROOT + " && (./venv/bin/python -c 'import web.api.main' >/dev/null 2>&1 && echo yes || echo no)",
               timeout=90) == "yes"


MIGRATE_CODE = r"""
import asyncio, sys
from sqlalchemy import text
from database.database import engine
async def main():
    sql = sys.stdin.read()
    async with engine.begin() as conn:
        for statement in [s.strip() for s in sql.split(';') if s.strip() and not s.strip().startswith('--')]:
            await conn.execute(text(statement))
        found = await conn.execute(text("select data_type, is_nullable, column_default from information_schema.columns "
                                        "where table_name='businesses' and column_name='studio_admin_access'"))
        row = found.first()
        print('column=' + ('present' if row else 'MISSING'))
        if not row:
            raise SystemExit(2)
    await engine.dispose()
asyncio.run(main())
"""


def sql_statements(path: str) -> str:
    """The migration without comment lines, so a statement is never glued to a comment."""
    return "\n".join(line for line in Path(path).read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    apply = os.environ.get("APPLY", "false").lower() == "true"
    client = None
    for attempt in range(3):
        candidate = paramiko.SSHClient()
        candidate.set_missing_host_key_policy(PinnedKey())
        try:
            candidate.connect(HOST, port=22, username="root", password=password, allow_agent=False,
                              look_for_keys=False, timeout=12, banner_timeout=25, auth_timeout=15)
            client = candidate
            break
        except Exception:
            candidate.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)
    sftp = client.open_sftp()
    stamp = str(int(time.time()))
    backups, created = {}, []
    dropin_made = False
    changed = False
    try:
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        if code_of(client, "/health") != "200":
            raise RuntimeError("Web API health was not 200 before the change; refusing")

        # 1. read-only checks: every patch must apply and compile
        originals = {path: read(sftp, path) for path in (MODELS, SCHEMAS, MAIN)}
        if any(value is None for value in originals.values()):
            raise RuntimeError("A production file to patch is missing")
        patched = {}
        for path, func in ((MODELS, patch_models), (SCHEMAS, patch_schemas), (MAIN, patch_main)):
            before = originals[path].decode("utf-8")
            after, needed = func(before)
            compile(after, path, "exec")
            patched[path] = (after, needed)
            print("%s: sha256_before=%s needs_patch=%s" % (path.replace(ROOT + "/", ""),
                  hashlib.sha256(originals[path]).hexdigest()[:16], "yes" if needed else "no"))
        new_files = {}
        for path, local in NEW_ROUTES.items():
            data = Path(local).read_bytes()
            current = read(sftp, path)
            new_files[path] = (data, current is None or current != data)
            print("%s: needs_copy=%s" % (local, "yes" if new_files[path][1] else "no"))
        # Importing the API needs the service's environment; if even today's code cannot be imported this way,
        # the check proves nothing and is skipped (reported, never silent).
        usable = import_ok(client)
        print("import_check_usable=" + ("yes" if usable else "no"))
        ready = handoff_ready(client)
        print("sign_in_secrets_prepared_by_studio=" + ("yes" if ready else "no"))
        if not apply:
            print("read_only_preflight=yes; production_untouched=yes")
            return
        if not ready:
            raise RuntimeError("The studio deploy has not created the sign-in secrets yet; deploy the studio first")

        # 2. migration (adds a column with a default; safe to run twice)
        statements = sql_statements(MIGRATION)
        out = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(MIGRATE_CODE)
                  + " <<'SQL'\n" + statements + "\nSQL", timeout=90)
        print(out)
        if "column=present" not in out:
            raise RuntimeError("Migration did not produce the column")
        changed = True

        # 3. backups and file changes
        for path, (after, needed) in patched.items():
            if needed:
                backups[path] = path + ".bak.studio." + stamp
                run(client, "cp -p " + shlex.quote(path) + " " + shlex.quote(backups[path]))
        for path, (data, needed) in new_files.items():
            if needed:
                if read(sftp, path) is not None:
                    backups[path] = path + ".bak.studio." + stamp
                    run(client, "cp -p " + shlex.quote(path) + " " + shlex.quote(backups[path]))
                else:
                    created.append(path)
        for path, (after, needed) in patched.items():
            if needed:
                put(sftp, path, after.encode("utf-8"), sftp.stat(path).st_mode & 0o777)
        for path, (data, needed) in new_files.items():
            if needed:
                put(sftp, path, data)
        files = " ".join(shlex.quote(p.replace(ROOT + "/", "")) for p in list(patched) + list(NEW_ROUTES))
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile " + files)
        if usable and not import_ok(client):
            raise RuntimeError("The patched API does not import")
        print("compile_and_import_check=ok")

        # 4. the web API reads the two secrets from the studio's handoff file
        run(client, "mkdir -p " + DROPIN_DIR)
        if read(sftp, DROPIN) != DROPIN_TEXT.encode():
            put(sftp, DROPIN, DROPIN_TEXT.encode())
            dropin_made = True
        run(client, "systemctl daemon-reload")

        # 5. restart and verify
        run(client, "systemctl restart " + SERVICE, timeout=60)
        wait_health(client)
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        access = code_of(client, "/internal/studio/access?user_id=0&business_id=0")
        schedule = code_of(client, "/internal/studio/schedule?business_id=0&date_from=2026-01-01&date_to=2026-01-01")
        print("internal_access_without_key=" + access + " internal_schedule_without_key=" + schedule)
        if access != "403" or schedule != "403":
            raise RuntimeError("New addresses do not answer as expected (403 means secrets are set and the key is checked)")
        print("backups=" + ",".join(sorted(set(os.path.basename(v) for v in backups.values()))))
        print("studio_backend_deployed=yes; web_api_health=200")
    except Exception:
        if changed:
            try:
                restore = []
                for path, backup in backups.items():
                    restore.append("cp -p %s %s" % (shlex.quote(backup), shlex.quote(path)))
                for path in created:
                    restore.append("rm -f " + shlex.quote(path))
                if dropin_made:
                    restore.append("rm -f " + shlex.quote(DROPIN))
                    restore.append("systemctl daemon-reload")
                restore.append("systemctl restart " + SERVICE)
                run(client, "set -e; " + "; ".join(restore), timeout=90)
                wait_health(client)
                print("studio_backend_rolled_back=yes")
            except Exception:
                print("studio_backend_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
