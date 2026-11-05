"""Scoped CRM backend rollout; deploy before merging the frontend to main.

Pinned SSH identity, reviewed-file checks, database/file backups, additive migration,
API smoke checks and file rollback. APPLY defaults to false.
"""
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.deploy_business_creation_and_reminders import (
    ROOT, WEB_SERVICE, BOT_SERVICE, classify, connect, put, run, sha,
    server_now, wait_web_ready, bot_polling_healthy,
)

BASELINE = {
    "database/models.py": "d68b322caa689318cc57089eda729cc7990188ef3044a53bd37c946a220b1013",
    "database/account_link.py": "25eaf3002994f91625f1d4e3ab2783b7b3314eea9bd16b88c13443a07f610226",
    "web/api/main.py": "792c1626d672dca32997c51814f5948264441a7197ba2f4531287f461af82a15",
}
NEW_FILES = ["web/api/routes/crm.py", "migrations/add_crm_contacts.sql"]
# Read the running API's environment without printing credentials. This also
# makes migrations and smoke tests use exactly the same database/JWT configuration.
ENV = '''import os, subprocess
pid = subprocess.check_output(['systemctl', 'show', '-p', 'MainPID', '--value', 'booking-web-api.service'], text=True).strip()
for entry in open('/proc/' + pid + '/environ', 'rb').read().split(b'\\0'):
    if b'=' in entry:
        key, value = entry.split(b'=', 1)
        os.environ[key.decode()] = value.decode()
from dotenv import load_dotenv
load_dotenv('/opt/booking_bot/.env')
'''
BACKUP = '''
from web.api.database import engine
url = engine.url
assert url.get_backend_name() == 'postgresql', 'Expected production PostgreSQL'
env = dict(os.environ, PGPASSWORD=url.password or '')
path = BACKUP_PATH
subprocess.run(['pg_dump', '-Fc', '-h', url.host or 'localhost', '-p', str(url.port or 5432),
                '-U', url.username, '-d', url.database, '-f', path], env=env, check=True, timeout=180)
os.chmod(path, 0o600)
assert os.path.getsize(path) > 0
print('database_backup=' + path)
'''
MIGRATE = '''
import asyncio
from sqlalchemy import text
from web.api.database import engine
async def main():
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL lock_timeout = '10s'"))
        for statement in MIGRATION_STATEMENTS:
            await conn.execute(text(statement))
        await conn.execute(text('SELECT id, business_id, user_id, notes, stage, follow_up_on FROM crm_contacts LIMIT 0'))
    await engine.dispose()
asyncio.run(main())
print('crm_migration=ok')
'''
SMOKE = '''
import asyncio, httpx
from sqlalchemy import select
from web.api.database import engine, async_session, Business
from web.api.deps import create_token
async def main():
    async with async_session() as db:
        business = await db.scalar(select(Business).order_by(Business.id).limit(1))
        assert business is not None, 'No business for read-only CRM smoke test'
        with httpx.Client(base_url='http://127.0.0.1:8000', timeout=15) as client:
            path = f'/admin/businesses/{business.id}/contacts'
            assert client.get(path).status_code == 401, 'Unauthenticated CRM request not rejected'
            headers = {'Authorization': 'Bearer ' + create_token(business.owner_id, 'admin')}
            response = client.get(path, headers=headers)
            assert response.status_code == 200, 'Authenticated CRM list failed'
            data = response.json()
            assert isinstance(data['items'], list) and isinstance(data['total'], int)
            if data['items']:
                response = client.get(path + '/' + data['items'][0]['key'], headers=headers)
                assert response.status_code == 200 and 'history' in response.json(), 'CRM detail failed'
    await engine.dispose()
asyncio.run(main())
print('crm_auth_guard=401; crm_owner_list=200; crm_read_smoke=ok')
'''


def python(client, code, timeout=60):
    return run(client, 'cd ' + ROOT + ' && ./venv/bin/python -c ' + shlex.quote(ENV + code), timeout)


def migration_statements(sql):
    # This migration has standalone line comments, including semicolons.
    sql = '\n'.join(line for line in sql.splitlines() if not line.lstrip().startswith('--'))
    return [statement.strip() for statement in sql.split(';') if statement.strip()]


def main():
    password = os.environ.get('SERVGO_PASSWORD')
    if not password:
        raise RuntimeError('Missing GitHub SSH secret')
    apply = os.environ.get('APPLY', 'false').lower() == 'true'
    repo = Path(__file__).resolve().parent.parent
    new = {p: (repo / p).read_bytes() for p in [*BASELINE, *NEW_FILES]}
    client = connect(password)
    sftp = client.open_sftp()
    backups, created = {}, []
    try:
        for service in (WEB_SERVICE, BOT_SERVICE):
            if run(client, 'systemctl is-active ' + service) != 'active':
                raise RuntimeError(service + ' inactive; refusing change')
        plan = {}
        for path in new:
            try:
                with sftp.open(ROOT + '/' + path, 'rb') as handle:
                    current = handle.read()
            except FileNotFoundError:
                if path in BASELINE:
                    raise
                plan[path] = 'create'
            else:
                if path in BASELINE:
                    plan[path] = classify(sha(current), BASELINE[path], sha(new[path]))
                elif current == new[path]:
                    plan[path] = 'skip'
                else:
                    raise RuntimeError(path + ' differs from reviewed content')
            print(path + ': ' + plan[path], flush=True)
        if not apply:
            print('crm_preflight=ok; production_untouched=yes')
            return
        stamp = str(int(time.time()))
        backup_path = '/opt/booking_bot/crm-before-' + stamp + '.dump'
        print(python(client, 'BACKUP_PATH = ' + repr(backup_path) + '\n' + BACKUP, 210), flush=True)
        sql = new['migrations/add_crm_contacts.sql'].decode()
        print(python(client, 'MIGRATION_STATEMENTS = ' + repr(migration_statements(sql)) + '\n' + MIGRATE), flush=True)
        todo = [p for p in new if plan[p] != 'skip']
        for path in todo:
            target = ROOT + '/' + path
            if plan[path] == 'deploy':
                backup = target + '.bak.crm.' + stamp
                run(client, 'cp -p ' + shlex.quote(target) + ' ' + shlex.quote(backup))
                backups[path] = backup
        for path in todo:
            target = ROOT + '/' + path
            if plan[path] == 'create':
                created.append(target)
            put(sftp, target, new[path], 0o644)
        print(python(client, 'import web.api.main\nweb.api.main.app.openapi()\nprint("api_import=ok")'), flush=True)
        run(client, 'systemctl restart ' + WEB_SERVICE, 60)
        wait_web_ready(client)
        print(python(client, SMOKE), flush=True)
        since = server_now(client)
        run(client, 'systemctl restart ' + BOT_SERVICE, 60)
        if not bot_polling_healthy(client, since):
            raise RuntimeError('Bot polling did not recover')
        print('crm_backend_deployed=yes; web_api_health=200; bot_polling=yes', flush=True)
    except Exception:
        if backups or created:
            commands = ['cp -p ' + shlex.quote(b) + ' ' + shlex.quote(ROOT + '/' + p) for p, b in backups.items()]
            commands += ['rm -f ' + shlex.quote(p) for p in created]
            try:
                run(client, 'set -e; ' + '; '.join(commands) + '; systemctl restart ' + WEB_SERVICE + '; systemctl restart ' + BOT_SERVICE, 90)
                wait_web_ready(client)
                print('crm_files_rolled_back=yes; additive_table_preserved=yes', flush=True)
            except Exception:
                print('crm_rollback_incomplete=yes', flush=True)
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == '__main__':
    main()
