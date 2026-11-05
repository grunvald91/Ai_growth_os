"""Read-only, redacted production photo diagnostics. Dispatch manually."""
import base64
import difflib
import hashlib
import logging
import os
from pathlib import Path
import re
import shlex
import subprocess
import time

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fingerprint != FP:
            raise paramiko.SSHException("Unexpected ServGo host key")
        client.get_host_keys().add(hostname, key.get_name(), key)


REMOTE = r'''import ast, asyncio, importlib.util, json, os, pathlib, subprocess
import httpx
root=pathlib.Path('/opt/booking_bot')
os.chdir(root)
for path in ['web/api/routes/admin.py','web/api/routes/client.py']:
    print(path+'_sha256='+__import__('hashlib').sha256((root/path).read_bytes()).hexdigest())
for unit in ['booking-web-api.service','booking-bot-relay89.service','booking-bot.service']:
    p=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True)
    print(unit+'_state='+p.stdout.strip())
pid=subprocess.run(['systemctl','show','-p','MainPID','--value','booking-web-api.service'],capture_output=True,text=True).stdout.strip()
env={}
if pid.isdigit() and int(pid)>0:
    for pair in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
        if b'=' in pair:
            k,v=pair.split(b'=',1)
            env[k.decode('utf-8','replace')]=v.decode('utf-8','replace')
print('web_token_present='+str(bool(env.get('BOT_TOKEN'))))
print('web_proxy_url_present='+str(bool(env.get('WEB_TELEGRAM_PROXY'))))
print('socksio_installed='+str(__import__('importlib').util.find_spec('socksio') is not None))
def literal_token(path,func):
    tree=ast.parse((root/path).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node,ast.AsyncFunctionDef) and node.name==func:
            for child in ast.walk(node):
                if isinstance(child,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='bot_token' for t in child.targets):
                    return child.value.value if isinstance(child.value,ast.Constant) else None
    return None
print('web_env_matches_admin_literal='+str(bool(env.get('BOT_TOKEN')) and env['BOT_TOKEN']==literal_token('web/api/routes/admin.py','upload_business_photo')))
async def probe():
    token=env.get('BOT_TOKEN')
    if not token:
        print('proxy_getMe=skipped_no_web_token')
        return
    try:
        async with httpx.AsyncClient(proxy='socks5://127.0.0.1:1089',timeout=10) as client:
            resp=await client.get('https://api.telegram.org/bot'+token+'/getMe')
            print('proxy_getMe_http='+str(resp.status_code)+' ok='+str(resp.json().get('ok')))
    except Exception as exc:
        print('proxy_getMe_error_type='+type(exc).__name__)
    file_id=None
    try:
        from sqlalchemy import text
        from database.database import async_session_maker
        async with async_session_maker() as session:
            result=await session.execute(text("SELECT photos FROM businesses WHERE photos IS NOT NULL ORDER BY id LIMIT 20"))
            for raw in result.scalars():
                photos=json.loads(raw)
                if isinstance(photos,list) and photos:
                    file_id=photos[0];break
    except Exception as exc:
        print('photo_lookup_error_type='+type(exc).__name__)
    if file_id:
        try:
            async with httpx.AsyncClient(proxy='socks5://127.0.0.1:1089',timeout=10) as client:
                r=await client.get('https://api.telegram.org/bot'+token+'/getFile',params={'file_id':file_id})
                print('proxy_getFile_http='+str(r.status_code)+' ok='+str(r.json().get('ok')))
        except Exception as exc:
            print('proxy_getFile_error_type='+type(exc).__name__)
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r=await client.get('http://127.0.0.1:8000/telegram-photo/'+file_id)
                print('local_photo_http='+str(r.status_code)+' content_type='+str(r.headers.get('content-type','')).split(';')[0])
        except Exception as exc:
            print('local_photo_error_type='+type(exc).__name__)
asyncio.run(probe())
'''


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    for attempt in range(3):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedKey())
        try:
            client.connect(HOST, port=22, username="root", password=password, allow_agent=False,
                           look_for_keys=False, timeout=12, banner_timeout=25, auth_timeout=15)
            command = "cd /opt/booking_bot && ./venv/bin/python -c " + shlex.quote(REMOTE)
            _, stdout, _ = client.exec_command(command, timeout=75)
            body = stdout.read().decode("utf-8", "replace")
            status = stdout.channel.recv_exit_status()
            if status:
                raise RuntimeError("Read-only remote diagnostics failed; stderr suppressed")
            print(body)
            with client.open_sftp() as sftp:
                with sftp.open("/opt/booking_bot/web/api/routes/admin.py", "rb") as source:
                    production = source.read().decode("utf-8", "replace")
            reviewed = subprocess.check_output(
                ["git", "show", "8cc9ba1:web/api/routes/admin.py"], text=True, encoding="utf-8"
            )
            delta = list(difflib.unified_diff(reviewed.splitlines(), production.splitlines(),
                                              fromfile="reviewed_admin", tofile="production_admin", n=2))
            print("admin_diff_lines=" + str(len(delta)))
            for line in delta[:150]:
                if re.search(r"token|password|secret|key\s*=|\d{7,12}:[A-Za-z0-9_-]{15,}", line, re.I):
                    line = line[:1] + "[REDACTED LINE]"
                print("admin_diff " + line)
            return
        except (paramiko.SSHException, OSError, EOFError):
            if attempt == 2:
                raise RuntimeError("SSH unavailable") from None
            time.sleep(3)
        finally:
            client.close()


if __name__ == "__main__":
    main()
