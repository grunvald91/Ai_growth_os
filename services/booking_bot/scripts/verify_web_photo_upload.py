"""Manually verify web media upload to Telegram; delete temporary message immediately."""
import base64
import hashlib
import logging
import os
import shlex
import struct
import time
import zlib

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


REMOTE = r'''import asyncio, os, pathlib, struct, subprocess, zlib
import httpx
pid=subprocess.run(['systemctl','show','-p','MainPID','--value','booking-web-api.service'],capture_output=True,text=True).stdout.strip()
env={}
for pair in pathlib.Path('/proc/'+pid+'/environ').read_bytes().split(b'\0'):
    if b'=' in pair:
        k,v=pair.split(b'=',1)
        env[k.decode('utf-8','replace')]=v.decode('utf-8','replace')
assert env.get('BOT_TOKEN') and env.get('WEB_TELEGRAM_PROXY')=='socks5://127.0.0.1:1089'
def chunk(kind,payload):
    return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload)&0xffffffff)
image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\x00'+b'\x4a\x95\xcf'*64)*64))+chunk(b'IEND',b'')
async def check():
    root='https://api.telegram.org/bot'+env['BOT_TOKEN']+'/'
    chat_id='437862772'
    async with httpx.AsyncClient(proxy=env['WEB_TELEGRAM_PROXY'],timeout=25) as http:
        sent=await http.post(root+'sendPhoto',data={'chat_id':chat_id,'caption':'ServGo: временная проверка загрузки фото'},files={'photo':('servgo-smoke.png',image,'image/png')})
        result=sent.json()
        print('sendPhoto_http='+str(sent.status_code)+' ok='+str(result.get('ok')))
        message_id=result.get('result',{}).get('message_id')
        if not message_id:
            raise RuntimeError('No temporary message id; photo upload smoke failed')
        try:
            photos=result['result'].get('photo') or []
            print('sendPhoto_photo_sizes='+str(len(photos)))
            assert sent.status_code==200 and result.get('ok') is True and photos,'Telegram did not accept photo'
        finally:
            deleted=await http.post(root+'deleteMessage',json={'chat_id':chat_id,'message_id':message_id})
            print('temporary_message_deleted='+str(deleted.status_code==200 and deleted.json().get('ok') is True))
            if deleted.status_code!=200 or not deleted.json().get('ok'):
                raise RuntimeError('Temporary Telegram message cleanup failed')
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
            _, stdout, _ = client.exec_command(
                "cd /opt/booking_bot && ./venv/bin/python -c " + shlex.quote(REMOTE), timeout=65
            )
            result = stdout.read().decode("utf-8", "replace")
            status = stdout.channel.recv_exit_status()
            if status:
                raise RuntimeError("Photo upload smoke failed; stderr suppressed; output=" + result.strip())
            print(result)
            return
        except (paramiko.SSHException, OSError, EOFError):
            if attempt == 2:
                raise RuntimeError("SSH unavailable") from None
            time.sleep(3)
        finally:
            client.close()


if __name__ == "__main__":
    main()
