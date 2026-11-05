"""Manual scoped rollout of sign-in with Yandex ID (web API only).

Production changes:
  * database/models.py       - User.birthday and two new tables (only inserted lines, via patch)
  * web/api/main.py          - import and include the new router (only inserted lines, via patch)
  * web/api/routes/yandex_auth.py - new file
  * web/api/routes/auth.py, web/api/routes/email_auth.py, database/account_link.py - whole-file replace, only if production equals the
    reviewed baseline (sha256 below); they join accounts of one person when the phone or email match
  * migrations/add_yandex_auth.sql - additive SQL (new columns/tables; safe to run twice)
  * /etc/servgo/yandex.env (0600, holds the OAuth secret) + a systemd drop-in that reads it
Restart: booking-web-api.service only. Backups, automatic rollback. The secret is never printed.
"""
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
ROOT = "/opt/booking_bot"
SERVICE = "booking-web-api.service"
MODELS = ROOT + "/database/models.py"
MAIN = ROOT + "/web/api/main.py"
ROUTE = ROOT + "/web/api/routes/yandex_auth.py"
# production path -> (repository path, sha256 of the version on main before this feature)
REPLACED = {
    ROOT + "/web/api/routes/auth.py": ("web/api/routes/auth.py",
                                       "a61b5bd602246a582dbc5a52e6d81bb008af5d64e373b061546cd803340392e5"),
    ROOT + "/web/api/routes/email_auth.py": ("web/api/routes/email_auth.py",
                                             "6c6d3ad2f422335bdec212befebeafa00b4e4769be11c569b78e4d35797af602"),
    ROOT + "/database/account_link.py": ("database/account_link.py",
                                         "cf453b64fab93a3155b0122bb9e717dfa532e0a76598886eb3a5acac22e6b960"),
}
ENV_DIR = "/etc/servgo"
ENV_FILE = ENV_DIR + "/yandex.env"
DROPIN_DIR = "/etc/systemd/system/" + SERVICE + ".d"
DROPIN = DROPIN_DIR + "/yandex-login.conf"
DROPIN_TEXT = "[Service]\nEnvironmentFile=-" + ENV_FILE + "\n"
MIGRATION = "migrations/add_yandex_auth.sql"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)

EMAIL_LINE = "    email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)\n    is_admin:"
BIRTHDAY_BLOCK = (
    "    # Дата рождения «ГГГГ-ММ-ДД» из Яндекс ID; если год неизвестен, он «0000». Для поздравлений.\n"
    "    birthday: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)\n"
)
CLASS_MARKER = "class EmailChallenge(Base):"
IMPORT_OLD = "studio_sso, studio_schedule\n"
INCLUDE_ANCHOR = 'app.include_router(telegram_link.router, prefix="/auth", tags=["auth"])\n'
INCLUDE_LINE = 'app.include_router(yandex_auth.router, prefix="/auth", tags=["auth"])\n'


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def new_classes(repository_models: str) -> str:
    """The two new model classes, exactly as reviewed in the repository."""
    a, b = repository_models.index("class YandexIdentity(Base):"), repository_models.index(CLASS_MARKER)
    return repository_models[a:b]


def patch_models(production: str, repository: str):
    """Insert User.birthday and the two classes. Returns (source, changed)."""
    if "class YandexIdentity(Base):" in production:
        return production, False
    if production.count(EMAIL_LINE) != 1 or production.count(CLASS_MARKER) != 1 or "birthday" in production:
        raise RuntimeError("Unexpected production models structure")
    result = production.replace(EMAIL_LINE, EMAIL_LINE.replace("    is_admin:", BIRTHDAY_BLOCK + "    is_admin:"), 1)
    result = result.replace(CLASS_MARKER, new_classes(repository) + CLASS_MARKER, 1)
    ast.parse(result)
    return result, True


def patch_main(production: str):
    """Import and include the router. Returns (source, changed)."""
    if "yandex_auth" in production:
        return production, False
    if production.count(IMPORT_OLD) != 1 or production.count(INCLUDE_ANCHOR) != 1:
        raise RuntimeError("Unexpected production main structure")
    result = production.replace(IMPORT_OLD, "studio_sso, studio_schedule, yandex_auth\n", 1)
    result = result.replace(INCLUDE_ANCHOR, INCLUDE_ANCHOR + INCLUDE_LINE, 1)
    ast.parse(result)
    return result, True


def replacement_needed(production, repository: bytes, baseline: str) -> bool:
    """True when production still has the reviewed baseline; False when it already has the new file."""
    if production == repository:
        return False
    if production is None or hashlib.sha256(production).hexdigest() != baseline:
        raise RuntimeError("Production file differs from the reviewed baseline; refusing to replace it")
    return True


def run(client, command: str, timeout: int = 60) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def read(sftp, path):
    try:
        with sftp.open(path, "rb") as source:
            return source.read()
    except FileNotFoundError:
        return None


def put(sftp, path: str, data: bytes, mode: int = 0o644):
    temp = path + ".yandex.tmp"
    try:
        sftp.remove(temp)
    except FileNotFoundError:
        pass
    with sftp.open(temp, "wx") as out:
        sftp.chmod(temp, mode)  # permissions first, secret bytes after
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


def sql_statements(path: str) -> str:
    return "\n".join(line for line in Path(path).read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("--"))


MIGRATE_CODE = r"""
import asyncio, sys
from sqlalchemy import text
from database.database import engine
async def main():
    sql = sys.stdin.read()
    async with engine.begin() as conn:
        for statement in [s.strip() for s in sql.split(';') if s.strip() and s.strip() not in ('BEGIN', 'COMMIT')]:
            await conn.execute(text(statement))
        for table in ('yandex_identities', 'yandex_tickets'):
            found = await conn.execute(text("select 1 from information_schema.tables where table_name=:t"), {'t': table})
            print(table + '=' + ('present' if found.first() else 'MISSING'))
        found = await conn.execute(text("select 1 from information_schema.columns where table_name='users' and column_name='birthday'"))
        print('users.birthday=' + ('present' if found.first() else 'MISSING'))
    await engine.dispose()
asyncio.run(main())
"""

SERVICE_ENV_CHECK = r"""import pathlib, subprocess
pid = subprocess.run(['systemctl', 'show', '-p', 'MainPID', '--value', '%s'], capture_output=True, text=True).stdout.strip()
env = {}
for pair in pathlib.Path('/proc/' + pid + '/environ').read_bytes().split(b'\0'):
    if b'=' in pair:
        k, v = pair.split(b'=', 1)
        env[k.decode('utf-8', 'replace')] = v.decode('utf-8', 'replace')
print('jwt_secret_long_enough=' + ('yes' if len(env.get('JWT_SECRET', '')) >= 32 else 'no'))
print('bot_token_present=' + ('yes' if env.get('BOT_TOKEN') else 'no'))
""" % SERVICE

VERIFY = r"""import httpx
from urllib.parse import parse_qs, urlparse
with httpx.Client(timeout=15, follow_redirects=False) as http:
    caps = http.get('http://127.0.0.1:8000/auth/yandex/capabilities').json()
    print('capabilities_available=' + str(caps.get('available')).lower())
    start = http.get('http://127.0.0.1:8000/auth/yandex/start')
    location = start.headers.get('location', '')
    url = urlparse(location)
    query = parse_qs(url.query)
    print('start_status=%%d host=%%s client_id_ok=%%s redirect_ok=%%s pkce=%%s cookie=%%s' %% (
        start.status_code, url.netloc, query.get('client_id') == ['%s'],
        query.get('redirect_uri') == ['https://servgo.ru/api/auth/yandex/callback'],
        query.get('code_challenge_method') == ['S256'], 'servgo_yx' in start.headers.get('set-cookie', '')))
    try:
        print('yandex_authorize_status=%%d' %% http.get(location).status_code)
    except Exception as exc:
        print('yandex_authorize_status=unreachable_from_server ' + type(exc).__name__)
    print('callback_without_state=' + http.get('http://127.0.0.1:8000/auth/yandex/callback', params={'code': 'x', 'state': 'y'}).headers.get('location', ''))
"""


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    client_id = os.environ.get("YANDEX_CLIENT_ID", "").strip()
    secret = os.environ.get("YANDEX_CLIENT_SECRET", "").strip()
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    if not client_id or not secret:
        raise RuntimeError("Missing YANDEX_CLIENT_ID or the YANDEX_CLIENT_SECRET GitHub secret; production untouched")
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
    backups, created, changed = {}, [], False
    try:
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        production_models = (read(sftp, MODELS) or b"").decode("utf-8")
        production_main = (read(sftp, MAIN) or b"").decode("utf-8")
        repository_models = Path("database/models.py").read_text(encoding="utf-8")
        models_after, models_changed = patch_models(production_models, repository_models)
        main_after, main_changed = patch_main(production_main)
        route_bytes = Path("web/api/routes/yandex_auth.py").read_bytes()
        route_current = read(sftp, ROUTE)
        if route_current is not None and route_current != route_bytes:
            raise RuntimeError("Unexpected existing yandex_auth.py on production")
        replacements = {}
        for remote, (local, baseline) in REPLACED.items():
            data = Path(local).read_bytes()
            if replacement_needed(read(sftp, remote), data, baseline):
                replacements[remote] = data
        print("models_patch_needed=%s main_patch_needed=%s route_needed=%s replaced=%s" % (
            "yes" if models_changed else "no", "yes" if main_changed else "no", "yes" if route_current is None else "no",
            ",".join(Path(p).name for p in replacements) or "none"))
        env_report = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(SERVICE_ENV_CHECK))
        print(env_report)
        if "jwt_secret_long_enough=yes" not in env_report:
            raise RuntimeError("Service JWT secret is too short for Yandex login; production untouched")

        # 1. additive migration (new tables/column; safe to run twice)
        out = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(MIGRATE_CODE)
                  + " <<'SQL'\n" + sql_statements(MIGRATION) + "\nSQL", timeout=90)
        print(out)
        if "MISSING" in out:
            raise RuntimeError("Migration did not produce the expected tables/column")
        changed = True

        # 2. backups, files, secret, drop-in
        for path, needed in ((MODELS, models_changed), (MAIN, main_changed), *((p, True) for p in replacements)):
            if needed:
                backups[path] = path + ".bak.yandex." + stamp
                run(client, "cp -p " + shlex.quote(path) + " " + shlex.quote(backups[path]))
        if route_current is None:
            created.append(ROUTE)
        if read(sftp, ENV_FILE) is None:
            created.append(ENV_FILE)
        else:
            backups[ENV_FILE] = ENV_FILE + ".bak.yandex." + stamp
            run(client, "cp -p " + shlex.quote(ENV_FILE) + " " + shlex.quote(backups[ENV_FILE]))
        if read(sftp, DROPIN) is None:
            created.append(DROPIN)
        run(client, "mkdir -p " + ENV_DIR + " && chmod 700 " + ENV_DIR + " && mkdir -p " + DROPIN_DIR)
        if models_changed:
            put(sftp, MODELS, models_after.encode("utf-8"), sftp.stat(MODELS).st_mode & 0o777)
        if main_changed:
            put(sftp, MAIN, main_after.encode("utf-8"), sftp.stat(MAIN).st_mode & 0o777)
        if route_current is None:
            put(sftp, ROUTE, route_bytes)
        for remote, data in replacements.items():
            put(sftp, remote, data, sftp.stat(remote).st_mode & 0o777)
        put(sftp, ENV_FILE, ("YANDEX_CLIENT_ID=%s\nYANDEX_CLIENT_SECRET=%s\n" % (client_id, secret)).encode(), 0o600)
        put(sftp, DROPIN, DROPIN_TEXT.encode())
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile database/models.py database/account_link.py "
            "web/api/main.py web/api/routes/auth.py web/api/routes/email_auth.py web/api/routes/yandex_auth.py")
        run(client, "systemctl daemon-reload && systemctl restart " + SERVICE, timeout=60)
        if run(client, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Web API failed after rollout")
        for _ in range(12):
            if run(client, "curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true") == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Web API did not become ready after rollout")
        report = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(VERIFY % client_id), timeout=90)
        print(report)
        if "capabilities_available=true" not in report or "client_id_ok=True" not in report \
                or "redirect_ok=True" not in report or "pkce=True" not in report \
                or "callback_without_state=/login?yandex=expired" not in report:
            raise RuntimeError("Yandex login verification failed")
        print("yandex_login_deployed=yes")
    except Exception:
        if changed:
            try:
                command = "set -e; systemctl stop " + SERVICE + "; "
                for path, backup in backups.items():
                    command += "cp -p " + shlex.quote(backup) + " " + shlex.quote(path) + "; "
                if created:
                    command += "rm -f " + " ".join(shlex.quote(p) for p in created) + "; "
                command += "rm -f " + " ".join(shlex.quote(p + ".yandex.tmp") for p in (MODELS, MAIN, ROUTE, ENV_FILE, DROPIN, *REPLACED)) + "; "
                command += "systemctl daemon-reload; systemctl restart " + SERVICE
                run(client, command, timeout=90)
                print("yandex_login_rolled_back=yes (new empty tables stay; they are harmless)")
            except Exception:
                print("yandex_login_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
