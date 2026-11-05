"""Manual scoped web-photo rollout with pinned SSH, backups and rollback."""
import ast
import base64
import hashlib
import logging
import os
from pathlib import Path
import re
import shlex
import time

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
ADMIN = ROOT + "/web/api/routes/admin.py"
CLIENT = ROOT + "/web/api/routes/client.py"
HELPER = ROOT + "/web/api/photo_transport.py"
DROPIN = "/etc/systemd/system/booking-web-api.service.d/photo-relay89.conf"
ADMIN_BASELINE = "48e9a8f21f2f5eedb41dd6f734f97360314bdd4890290aef88bb2e268c45b909"
CLIENT_BASELINE = "33fb7565e1675116e1de9a07296ce6f8d902ff4638c3e98859b3cb6b4f3a7873"
DROPIN_TEXT = "[Service]\nEnvironment=WEB_TELEGRAM_PROXY=socks5://127.0.0.1:1089\n"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def patch_admin_source(source: str) -> str:
    """Patch only photo functions of the verified production admin.py."""
    lines = source.splitlines(keepends=True)
    for name in ("upload_business_photo", "upload_employee_photo"):
        tree = ast.parse("".join(lines))
        node = next(x for x in ast.walk(tree) if isinstance(x, ast.AsyncFunctionDef) and x.name == name)
        part = "".join(lines[node.lineno - 1:node.end_lineno])
        old = "async with httpx.AsyncClient() as http:"
        if part.count(old) != 2:
            raise RuntimeError("Unexpected production photo transport structure: " + name)
        part = part.replace(old, "async with create_photo_client() as http:")
        part, count = re.subn(
            r'(?m)^    bot_token = "[^"\n]+"$',
            '    bot_token = os.getenv("BOT_TOKEN")\n    if not bot_token:\n        raise HTTPException(status_code=500, detail="Bot token not configured")',
            part,
        )
        if count != 1:
            raise RuntimeError("Unexpected production photo token structure: " + name)
        lines[node.lineno - 1:node.end_lineno] = part.splitlines(keepends=True)
    result = "".join(lines)
    if result.count("import logging\n") != 1 or result.count("from ..database import (") != 1:
        raise RuntimeError("Unexpected production admin import structure")
    result = result.replace("import logging\n", "import logging\nimport os\n", 1)
    result = result.replace("from ..database import (", "from ..photo_transport import create_photo_client\nfrom ..database import (", 1)
    ast.parse(result)
    return result


def run(client, command: str, timeout: int = 45) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def put(sftp, path: str, data: bytes, mode: int = 0o644):
    temp = path + ".photo-relay.tmp"
    with sftp.open(temp, "wx") as out:
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


VERIFY = r'''import asyncio, json, os, pathlib, subprocess
import httpx
from sqlalchemy import text
from database.database import async_session_maker
pid=subprocess.run(['systemctl','show','-p','MainPID','--value','booking-web-api.service'],capture_output=True,text=True).stdout.strip()
env={}
for pair in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in pair:
        k,v=pair.split(b'=',1)
        env[k.decode('utf-8','replace')]=v.decode('utf-8','replace')
assert env.get('WEB_TELEGRAM_PROXY')=='socks5://127.0.0.1:1089','web-only proxy not active'
assert env.get('BOT_TOKEN'),'web Bot API token missing'
async def check():
    async with httpx.AsyncClient(proxy=env['WEB_TELEGRAM_PROXY'],timeout=12) as http:
        response=await http.get('https://api.telegram.org/bot'+env['BOT_TOKEN']+'/getMe')
        assert response.status_code==200 and response.json().get('ok') is True,'Bot API not reachable via web proxy'
    print('web_proxy_getMe_ok=yes')
    async with async_session_maker() as session:
        rows=await session.execute(text('SELECT photos FROM businesses WHERE photos IS NOT NULL ORDER BY id LIMIT 20'))
        file_id=next((photos[0] for raw in rows.scalars() if isinstance((photos:=json.loads(raw)),list) and photos),None)
    assert file_id,'no existing business photo to verify'
    async with httpx.AsyncClient(timeout=25) as http:
        image=await http.get('http://127.0.0.1:8000/telegram-photo/'+file_id)
    image_bytes=image.content
    jpeg=image_bytes.startswith(bytes.fromhex('ffd8ff'))
    png=image_bytes.startswith(bytes.fromhex('89504e470d0a1a0a'))
    webp=image_bytes.startswith(b'RIFF') and image_bytes[8:12]==b'WEBP'
    assert image.status_code==200 and image.headers.get('content-type','').startswith('image/') and (jpeg or png or webp), 'existing business photo not served as an image'
    print('local_existing_business_photo_ok=yes bytes='+str(len(image_bytes)))
asyncio.run(check())'''


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing GitHub SSH secret")
    client = None
    for attempt in range(3):
        candidate = paramiko.SSHClient()
        candidate.set_missing_host_key_policy(PinnedKey())
        try:
            candidate.connect(HOST, port=22, username="root", password=password,
                              allow_agent=False, look_for_keys=False, timeout=12,
                              banner_timeout=25, auth_timeout=15)
            client = candidate
            break
        except Exception:
            candidate.close()
            if attempt == 2:
                raise RuntimeError("ServGo SSH unavailable") from None
            time.sleep(3)
    sftp = client.open_sftp()
    changed = False
    stamp = str(int(time.time()))
    backups = [(ADMIN, ADMIN + ".bak.photo." + stamp),
               (CLIENT, CLIENT + ".bak.photo." + stamp)]
    try:
        if run(client, "systemctl is-active booking-bot-relay89.service") != "active":
            raise RuntimeError("Private SOCKS relay inactive")
        if run(client, "systemctl is-active booking-web-api.service") != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        originals = {}
        for path, expected in [(ADMIN, ADMIN_BASELINE), (CLIENT, CLIENT_BASELINE)]:
            with sftp.open(path, "rb") as source:
                originals[path] = source.read()
            if hashlib.sha256(originals[path]).hexdigest() != expected:
                raise RuntimeError("Production source diverged from reviewed baseline: " + path.rsplit("/", 1)[-1])
        for path in (HELPER, DROPIN):
            try:
                sftp.stat(path)
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError("Unexpected existing web photo configuration")
        admin_bytes = patch_admin_source(originals[ADMIN].decode("utf-8")).encode("utf-8")
        client_bytes = Path("web/api/routes/client.py").read_bytes()
        helper_bytes = Path("web/api/photo_transport.py").read_bytes()
        if b"create_photo_client()" not in client_bytes or b"WEB_TELEGRAM_PROXY" not in helper_bytes:
            raise RuntimeError("Incomplete local web photo code")
        run(client, ROOT + "/venv/bin/python -m pip install --disable-pip-version-check --no-input 'socksio==1.0.0' >/dev/null", timeout=140)
        run(client, ROOT + "/venv/bin/python -c 'import socksio; print(\"photo_socks_dependency_ok=yes\")'")
        run(client, "mkdir -p /etc/systemd/system/booking-web-api.service.d")
        for path, backup in backups:
            run(client, "cp -p " + shlex.quote(path) + " " + shlex.quote(backup))
        changed = True
        put(sftp, ADMIN, admin_bytes, sftp.stat(ADMIN).st_mode & 0o777)
        put(sftp, CLIENT, client_bytes, sftp.stat(CLIENT).st_mode & 0o777)
        put(sftp, HELPER, helper_bytes)
        put(sftp, DROPIN, DROPIN_TEXT.encode())
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile web/api/routes/admin.py web/api/routes/client.py web/api/photo_transport.py")
        run(client, "systemctl daemon-reload && systemctl restart booking-web-api.service", timeout=45)
        if run(client, "systemctl is-active booking-web-api.service") != "active":
            raise RuntimeError("Web API failed after rollout")
        for attempt in range(10):
            if run(client, "curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true") == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Web API did not become ready after rollout")
        report = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(VERIFY), timeout=75)
        print(report)
        if "local_existing_business_photo_ok=yes" not in report:
            raise RuntimeError("Existing business photo verification failed")
        print("web_photo_fix_deployed=yes")
    except Exception:
        if changed:
            try:
                command = "set -e; systemctl stop booking-web-api.service; "
                for path, backup in backups:
                    command += "cp -p " + shlex.quote(backup) + " " + shlex.quote(path) + "; "
                command += "rm -f " + " ".join(shlex.quote(p) for p in
                    (HELPER, DROPIN, ADMIN + ".photo-relay.tmp", CLIENT + ".photo-relay.tmp",
                     HELPER + ".photo-relay.tmp", DROPIN + ".photo-relay.tmp"))
                command += "; systemctl daemon-reload; systemctl restart booking-web-api.service"
                run(client, command, timeout=60)
                print("web_photo_source_and_unit_rolled_back=yes")
            except Exception:
                print("web_photo_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
