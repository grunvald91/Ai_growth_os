"""Read-only, redacted room-photo bot flow diagnostics."""
import base64
import hashlib
import logging
import os
import shlex
import time

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


REMOTE = r'''import ast, asyncio, datetime, hashlib, json, pathlib, re, subprocess
from sqlalchemy import text
from database.database import async_session_maker
path=pathlib.Path('/opt/booking_bot/bot/handlers/admin.py')
print('admin_sha256='+hashlib.sha256(path.read_bytes()).hexdigest())
source=path.read_text()
tree=ast.parse(source)
for name in ('edit_emp_photos_menu','emp_add_photo_start','emp_add_photo_receive','emp_photos_done','edit_employee_menu'):
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name==name)
    segment=ast.get_source_segment(source,node)
    print('handler_sha256 '+name+'='+hashlib.sha256(segment.encode()).hexdigest())
for unit in ('booking-bot.service','booking-bot-relay89.service'):
    p=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True)
    print(unit+'_state='+p.stdout.strip())
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','-8 hours','--no-pager','-o','json'],capture_output=True,text=True,timeout=18)
if p.returncode: raise RuntimeError('journal unavailable')
entries=[json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
print('journal_entries='+str(len(entries)))
for i,entry in enumerate(entries):
    msg=str(entry.get('MESSAGE',''))
    if 'Update id=' not in msg: continue
    stamp=datetime.datetime.fromtimestamp(int(entry.get('__REALTIME_TIMESTAMP',0))/1000000,datetime.timezone(datetime.timedelta(hours=3)))
    if stamp.hour==15 and 20<=stamp.minute<=40:
        kind='unhandled' if 'not handled' in msg else 'handled'
        print('photo_window_update='+str(i)+' msk='+stamp.strftime('%H:%M:%S')+' '+kind)
for i,entry in enumerate(entries):
    msg=str(entry.get('MESSAGE',''))
    if 'emp_add_photo:' in msg or 'emp_photos_done:' in msg or 'edit_emp_photos:' in msg:
        matched=re.search(r'(emp_add_photo|emp_photos_done|edit_emp_photos):',msg)
        stamp=datetime.datetime.fromtimestamp(int(entry.get('__REALTIME_TIMESTAMP',0))/1000000,datetime.timezone(datetime.timedelta(hours=3)))
        print('photo_callback='+str(i)+' msk='+stamp.strftime('%H:%M:%S')+' kind='+matched.group(1))
    if 'Traceback (most recent call last)' in msg or 'TypeError:' in msg or 'TelegramBadRequest:' in msg:
        block='\n'.join(str(e.get('MESSAGE','')) for e in entries[i:min(i+35,len(entries))])
        frames=re.findall(r'File "[^"\n]*/([^/"\n]+\.py)", line (\d+)',block)
        classes=re.findall(r'(?m)^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):',block)
        if any('admin.py'==f for f,n in frames) or 'TypeError' in msg:
            print('bot_error='+str(i)+' frames='+','.join(f+':'+n for f,n in frames[-8:])+' types='+','.join(classes[:3]))
for center in (722,733,773,833):
    for i in range(center-2,min(center+3,len(entries))):
        msg=str(entries[i].get('MESSAGE',''))
        if 'Callback: data=' in msg: kind='callback'
        elif 'Update id=' in msg: kind='unhandled' if 'not handled' in msg else 'handled'
        elif 'Traceback' in msg: kind='traceback'
        elif 'ERROR' in msg: kind='error'
        else: kind='other'
        print('event_near_photo='+str(i)+' kind='+kind+' level='+str(entries[i].get('PRIORITY','?')))
for center in (775,835):
    block='\n'.join(str(e.get('MESSAGE','')) for e in entries[center:min(center+55,len(entries))])
    frames=re.findall(r'File "[^"\n]*/([^/"\n]+\.py)", line (\d+)',block)
    classes=re.findall(r'(?m)^\s*(?:[A-Za-z_][A-Za-z0-9_.]*\.)?([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception)):',block)
    print('photo_callback_error='+str(center)+' frames='+','.join(f+':'+n for f,n in frames[:20])+' types='+','.join(classes[:8]))
async def check():
    async with async_session_maker() as session:
        rows=await session.execute(text("SELECT b.id,e.id,e.photos FROM businesses b JOIN employees e ON e.business_id=b.id WHERE b.name ILIKE '%100boats%' ORDER BY e.id"))
        for b_id,e_id,raw in rows:
            try: count=len(json.loads(raw)) if raw else 0
            except (ValueError,TypeError): count=-1
            print('demo_room business_id='+str(b_id)+' employee_id='+str(e_id)+' photo_count='+str(count))
asyncio.run(check())'''


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    for attempt in range(3):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedKey())
        try:
            client.connect(HOST, port=22, username="root", password=password,
                           allow_agent=False, look_for_keys=False, timeout=12,
                           banner_timeout=25, auth_timeout=15)
            _, stdout, _ = client.exec_command("cd /opt/booking_bot && ./venv/bin/python -c " + shlex.quote(REMOTE), timeout=40)
            result = stdout.read().decode("utf-8", "replace")
            if stdout.channel.recv_exit_status():
                raise RuntimeError("Read-only bot photo probe failed; stderr suppressed")
            print(result)
            return
        except (paramiko.SSHException, OSError, EOFError):
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)
        finally:
            client.close()


if __name__ == "__main__":
    main()
