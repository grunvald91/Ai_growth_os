"""Manual scoped rollout: small cached copies of Telegram photos for the web API.

Changes on production, nothing else:
  * web/api/photo_cache.py (new file)
  * the /telegram-photo handler block inside web/api/routes/client.py (replaced only if it is
    byte-identical to the reviewed old block)
Restart: booking-web-api.service only. Backup + automatic rollback. No migrations.
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
CLIENT = ROOT + "/web/api/routes/client.py"
CACHE_MODULE = ROOT + "/web/api/photo_cache.py"
START = "# ---------- GET /telegram-photo/{file_id:path} ----------"
END = "# ---------- Shared prepayment/receipt logic"
OLD_BLOCK_SHA = "824cd8688bd36970311a1f5313253461b94da08263c41010cc438570c5b26151"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FP:
            raise paramiko.SSHException("ServGo SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def block_of(source: str) -> tuple[int, int]:
    if source.count(START) != 1 or source.count(END) != 1:
        raise RuntimeError("Unexpected photo handler structure")
    a, b = source.index(START), source.index(END)
    if a > b:
        raise RuntimeError("Unexpected photo handler order")
    return a, b


def new_client_source(production: str, repository: str) -> str:
    a, b = block_of(production)
    if hashlib.sha256(production[a:b].encode("utf-8")).hexdigest() != OLD_BLOCK_SHA:
        raise RuntimeError("Production photo handler differs from the reviewed old version")
    ra, rb = block_of(repository)
    result = production[:a] + repository[ra:rb] + production[b:]
    ast.parse(result)
    for needed in ("Optional", "Query"):
        if needed not in production.split("router = ", 1)[0]:
            raise RuntimeError("Production client.py lacks import: " + needed)
    return result


def run(client, command: str, timeout: int = 45) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    body = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Production command failed; stderr suppressed")
    return body


def put(sftp, path: str, data: bytes, mode: int = 0o644):
    temp = path + ".thumbs.tmp"
    with sftp.open(temp, "wx") as out:
        out.write(data)
    sftp.chmod(temp, mode)
    sftp.posix_rename(temp, path)


VERIFY = r'''import asyncio, json, time
import httpx
from sqlalchemy import text
from database.database import async_session_maker
async def check():
    async with async_session_maker() as session:
        rows=await session.execute(text('SELECT photos FROM businesses WHERE photos IS NOT NULL ORDER BY id LIMIT 20'))
        ids=[]
        for raw in rows.scalars():
            try:
                photos=json.loads(raw)
            except ValueError:
                photos=[raw]
            if isinstance(photos,list) and photos and isinstance(photos[0],str):
                ids.append(photos[0])
        ids=ids[:4]
    assert ids,'no existing business photo to verify'
    async with httpx.AsyncClient(timeout=40) as http:
        for file_id in ids:
            t0=time.time(); first=await http.get('http://127.0.0.1:8000/telegram-photo/'+file_id,params={'size':'thumb'}); t1=time.time()
            second=await http.get('http://127.0.0.1:8000/telegram-photo/'+file_id,params={'size':'thumb'}); t2=time.time()
            body=first.content
            assert first.status_code==200 and body[:4]==b'RIFF' and body[8:12]==b'WEBP','thumb is not WEBP'
            assert second.content==body and 'immutable' in second.headers.get('cache-control',''),'second answer differs'
            assert len(body)<60000,'thumb too large'
            print('thumb_ok=yes bytes=%d first_s=%.2f second_s=%.2f'%(len(body),t1-t0,t2-t1))
        full=await http.get('http://127.0.0.1:8000/telegram-photo/'+ids[0])
        assert full.status_code==200 and full.content[:3]==bytes.fromhex('ffd8ff') and len(full.content)>100000,'full photo changed'
        print('full_photo_ok=yes bytes=%d'%len(full.content))
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
    backup = CLIENT + ".bak.thumbs." + str(int(time.time()))
    try:
        if run(client, "systemctl is-active booking-web-api.service") != "active":
            raise RuntimeError("Web API was inactive; refusing change")
        with sftp.open(CLIENT, "rb") as source:
            production = source.read().decode("utf-8")
        repository = Path("web/api/routes/client.py").read_text(encoding="utf-8")
        client_bytes = new_client_source(production, repository).encode("utf-8")
        cache_bytes = Path("web/api/photo_cache.py").read_bytes()
        try:
            sftp.stat(CACHE_MODULE)
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("Unexpected existing photo cache module")
        # Pillow with WEBP must already exist; nothing is installed on production here.
        run(client, ROOT + "/venv/bin/python -c 'from PIL import features; assert features.check(\"webp\"); print(\"pillow_webp_ok=yes\")'")
        print(run(client, "systemctl show -p User -p ProtectSystem -p ReadWritePaths booking-web-api.service"))
        user = run(client, "systemctl show -p User --value booking-web-api.service") or "root"
        run(client, "mkdir -p /var/lib/servgo-photo-cache && chmod 755 /var/lib/servgo-photo-cache && chown "
            + shlex.quote(user) + " /var/lib/servgo-photo-cache")
        run(client, "cp -p " + shlex.quote(CLIENT) + " " + shlex.quote(backup))
        changed = True
        put(sftp, CACHE_MODULE, cache_bytes)
        put(sftp, CLIENT, client_bytes, sftp.stat(CLIENT).st_mode & 0o777)
        run(client, "cd " + ROOT + " && ./venv/bin/python -m py_compile web/api/photo_cache.py web/api/routes/client.py")
        run(client, "systemctl restart booking-web-api.service", timeout=45)
        if run(client, "systemctl is-active booking-web-api.service") != "active":
            raise RuntimeError("Web API failed after rollout")
        for attempt in range(10):
            if run(client, "curl -sS --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health || true") == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Web API did not become ready after rollout")
        report = run(client, "cd " + ROOT + " && ./venv/bin/python -c " + shlex.quote(VERIFY), timeout=150)
        print(report)
        if "thumb_ok=yes" not in report or "full_photo_ok=yes" not in report:
            raise RuntimeError("Photo verification failed")
        print("cache_folder_files=" + run(client, "ls /var/lib/servgo-photo-cache 2>/dev/null | wc -l"))
        print("photo_thumbnails_deployed=yes backup=" + backup.rsplit("/", 1)[-1])
    except Exception:
        if changed:
            try:
                run(client, "set -e; systemctl stop booking-web-api.service; cp -p " + shlex.quote(backup) + " " + shlex.quote(CLIENT)
                    + "; rm -f " + " ".join(shlex.quote(p) for p in (CACHE_MODULE, CACHE_MODULE + ".thumbs.tmp", CLIENT + ".thumbs.tmp"))
                    + "; systemctl restart booking-web-api.service", timeout=60)
                print("photo_thumbnails_rolled_back=yes")
            except Exception:
                print("photo_thumbnails_rollback_incomplete=yes")
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
