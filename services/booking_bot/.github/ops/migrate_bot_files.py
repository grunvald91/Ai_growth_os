"""One-off: move Telegram files from the old bot to the new bot (owner-approved 2026-10-20).

file_ids only work for the bot that uploaded them. For every stored file_id the new bot
cannot read, download it with the old bot and re-upload it with the new bot (message to the
owner's chat, deleted right after), then rewrite the ids in ONE DB transaction.
Backup of the affected tables first. Tokens never printed; the old token arrives via stdin.
"""
import base64, hashlib, os, time
import paramiko

HOST, FP = '155.212.162.47', 'uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU'

class Pinned(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        if base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=') != FP:
            raise paramiko.SSHException('Host key mismatch')
        client.get_host_keys().add(hostname, key.get_name(), key)

REMOTE_PY = r'''
import asyncio, json, pathlib, subprocess, sys
import httpx
from sqlalchemy import text
from database.database import async_session_maker

OLD = sys.stdin.readline().strip()
pid = subprocess.run(['systemctl','show','-p','MainPID','--value','booking-web-api.service'],capture_output=True,text=True).stdout.strip()
env = dict(p.split(b'=',1) for p in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0') if b'=' in p)
NEW = env[b'BOT_TOKEN'].decode()
PROXY = env.get(b'WEB_TELEGRAM_PROXY', b'').decode() or 'socks5://127.0.0.1:1089'
CHAT = 437862772
API = 'https://api.telegram.org/bot'
FILE = 'https://api.telegram.org/file/bot'
if not OLD or not NEW or OLD == NEW:
    print('TOKENS_MISSING_OR_IDENTICAL'); sys.exit(2)

COLUMNS = [  # table, column, is_json_list, kind
    ('businesses','photos',True,'photo'), ('businesses','video',False,'video'),
    ('businesses','cancellation_policy_photo',False,'photo'),
    ('employees','photos',True,'photo'), ('employees','primary_photo',False,'photo'),
    ('employees','video',False,'video'), ('client_subscriptions','payment_receipt',False,'document'),
]

def is_tg(v):
    return isinstance(v,str) and v and not v.startswith(('http://','https://','/'))

def values(val, is_list):
    if is_list:
        try: lst = json.loads(val)
        except Exception: return [val]
        return lst if isinstance(lst,list) else [val]
    return [val]

async def call(c, token, method, **kw):
    for attempt in range(6):
        try:
            r = await c.post(API+token+'/'+method, **kw)
            d = r.json()
        except Exception as exc:
            await asyncio.sleep(2); last = type(exc).__name__; continue
        if d.get('ok'): return d['result']
        ra = (d.get('parameters') or {}).get('retry_after')
        if ra: await asyncio.sleep(ra+1); continue
        return {'_error': str(d.get('description'))[:80]}
    return {'_error':'network/retry limit'}

def bump(errors, key):
    errors[key] = errors.get(key,0)+1

async def main():
    rows = {}
    async with async_session_maker() as s:
        for t,col,_,_ in COLUMNS:
            rows[(t,col)] = (await s.execute(text(f"SELECT id, {col} FROM {t} WHERE {col} IS NOT NULL AND {col} <> ''"))).all()
    need = {}
    for t,col,is_list,kind in COLUMNS:
        for _id,val in rows[(t,col)]:
            for v in values(val, is_list):
                if is_tg(v): need.setdefault(v,kind)
    print('stored_file_ids='+str(len(need)))
    mapping, stats, errors = {}, {'already_new':0,'moved':0,'failed':0}, {}
    async with httpx.AsyncClient(proxy=PROXY, timeout=90) as c:
        me_old = await call(c, OLD, 'getMe'); me_new = await call(c, NEW, 'getMe')
        print('old_bot=@'+str(me_old.get('username', me_old.get('_error')))+' new_bot=@'+str(me_new.get('username', me_new.get('_error'))))
        if '_error' in me_old: print('OLD_TOKEN_INVALID'); return
        for fid,kind in need.items():
            if '_error' not in await call(c, NEW, 'getFile', data={'file_id':fid}):
                stats['already_new']+=1; continue
            info = await call(c, OLD, 'getFile', data={'file_id':fid})
            if '_error' in info:
                stats['failed']+=1; bump(errors,'old getFile: '+info['_error']); continue
            path = info['file_path']
            try:
                r = await c.get(FILE+OLD+'/'+path)
            except Exception as exc:
                stats['failed']+=1; bump(errors,'download '+type(exc).__name__); continue
            if r.status_code != 200:
                stats['failed']+=1; bump(errors,'download '+str(r.status_code)); continue
            name = path.rsplit('/',1)[-1]
            method, field = {'photo':('sendPhoto','photo'),'video':('sendVideo','video'),'document':('sendDocument','document')}[kind]
            if kind=='document' and name.lower().endswith(('.jpg','.jpeg','.png','.webp')): method, field = 'sendPhoto','photo'
            res = await call(c, NEW, method, data={'chat_id':str(CHAT),'disable_notification':'true'}, files={field:(name, r.content)})
            if '_error' in res and method != 'sendDocument':
                res = await call(c, NEW, 'sendDocument', data={'chat_id':str(CHAT),'disable_notification':'true'}, files={'document':(name, r.content)})
            if '_error' in res:
                stats['failed']+=1; bump(errors,'upload: '+res['_error']); continue
            new_id = (res['photo'][-1]['file_id'] if res.get('photo') else
                      (res.get('video') or res.get('document') or res.get('animation') or {}).get('file_id'))
            if not new_id:
                stats['failed']+=1; bump(errors,'upload: no file in reply'); continue
            mapping[fid] = new_id; stats['moved']+=1
            await call(c, NEW, 'deleteMessage', data={'chat_id':str(CHAT),'message_id':str(res['message_id'])})
            await asyncio.sleep(1.1)
    print('result '+json.dumps(stats))
    if errors: print('errors '+json.dumps(errors, ensure_ascii=False))
    if not mapping: print('nothing_to_update'); return
    updated = 0
    async with async_session_maker() as s:
        async with s.begin():
            for t,col,is_list,_ in COLUMNS:
                for _id,val in rows[(t,col)]:
                    if is_list:
                        try: lst = json.loads(val)
                        except Exception: lst = None
                        if isinstance(lst,list):
                            new = [mapping.get(v,v) if isinstance(v,str) else v for v in lst]
                            if new != lst:
                                await s.execute(text(f"UPDATE {t} SET {col}=:v WHERE id=:i"), {'v':json.dumps(new, ensure_ascii=False),'i':_id}); updated+=1
                            continue
                    if val in mapping:
                        await s.execute(text(f"UPDATE {t} SET {col}=:v WHERE id=:i"), {'v':mapping[val],'i':_id}); updated+=1
    print('db_rows_updated='+str(updated))

asyncio.run(main())
'''

PREP = r'''
set -e
B=/opt/booking_bot/backups/bot-files-20261020
mkdir -p $B && cd /tmp
sudo -u postgres pg_dump -d booking_bot -t businesses -t employees -t client_subscriptions | gzip > $B/tables-before.sql.gz
echo "backup_bytes=$(stat -c %s $B/tables-before.sql.gz)"
'''

def main():
    old = os.environ.get('OLD_BOT_TOKEN', '').strip()
    if not old:
        raise SystemExit('OLD token secret is empty')
    for attempt in range(3):
        c = paramiko.SSHClient(); c.set_missing_host_key_policy(Pinned())
        try:
            c.connect(HOST, username='root', password=os.environ['SERVER_PASSWORD'], look_for_keys=False,
                      allow_agent=False, timeout=15, banner_timeout=30, auth_timeout=20)
            break
        except (paramiko.SSHException, OSError, EOFError):
            c.close()
            if attempt == 2: raise
            time.sleep(3)
    try:
        _, out, _ = c.exec_command(PREP, timeout=120)
        print(out.read().decode(errors='replace'))
        if out.channel.recv_exit_status():
            raise SystemExit('BACKUP_FAILED')
        with c.open_sftp() as sftp:
            with sftp.open('/tmp/migrate_bot_files.py', 'w') as f:
                f.write(REMOTE_PY)
        stdin, out, err = c.exec_command('cd /opt/booking_bot && PYTHONPATH=/opt/booking_bot ./venv/bin/python /tmp/migrate_bot_files.py; rc=$?; rm -f /tmp/migrate_bot_files.py; exit $rc', timeout=1500)
        stdin.write(old + '\n'); stdin.flush(); stdin.channel.shutdown_write()
        text = out.read().decode(errors='replace')
        status = out.channel.recv_exit_status()
        for line in text.splitlines():
            print(line.replace(old, '[REDACTED]'))
        if status:
            print(err.read().decode(errors='replace').replace(old, '[REDACTED]')[-1500:])
            raise SystemExit('MIGRATION_FAILED')
    finally:
        c.close()

if __name__ == '__main__':
    main()
