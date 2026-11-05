"""Read-only redacted diagnosis of bot rollout traceback and rollback state."""
import base64
import hashlib
import logging
import os
import shlex
import time

import paramiko

HOST = "155.212.162.47"
FINGERPRINT = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class Pinned(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fp != FINGERPRINT:
            raise paramiko.SSHException("Host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


REMOTE = '''import json,subprocess,re,pathlib,hashlib
root=pathlib.Path('/opt/booking_bot')
for name,path in [('main',root/'bot/main.py'),('transport',root/'bot/telegram_transport.py'),('dropin',pathlib.Path('/etc/systemd/system/booking-bot.service.d/relay89.conf'))]:
    print(name+'_present='+str(path.is_file()))
    if name=='main' and path.is_file(): print('main_sha256='+hashlib.sha256(path.read_bytes()).hexdigest())
print('client_sha256='+hashlib.sha256((root/'bot/handlers/client.py').read_bytes()).hexdigest())
for unit in ('booking-bot.service','booking-bot-relay89.service'):
    p=subprocess.run(['systemctl','is-active',unit],capture_output=True,text=True,timeout=5)
    print(unit+'_state='+p.stdout.strip())
p=subprocess.run(['journalctl','-u','booking-bot.service','--since','-12 minutes','--no-pager','-o','json'],capture_output=True,text=True,timeout=12)
if p.returncode: raise RuntimeError('journal unavailable')
entries=[json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
messages=[str(e.get('MESSAGE','')) for e in entries]
print('journal_entry_count='+str(len(messages)))
print('polling_started_events='+str(sum('Run polling for bot' in s for s in messages)))
print('network_error_events='+str(sum('TelegramNetworkError' in s or 'ClientConnectorError' in s for s in messages)))
for n,msg in enumerate(messages):
    if 'Traceback (most recent call last):' not in msg: continue
    block='\\n'.join(messages[n:min(n+60,len(messages))]).splitlines()
    frames=[]; classes=[]
    for line in block:
        match=re.search(r'File "[^"\\n]*/([^/"\\n]+\\.py)", line ([0-9]+)',line)
        if match: frames.append(match.group(1)+':'+match.group(2))
        typ=re.match(r'^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception)):',line.strip())
        if typ: classes.append(typ.group(1))
    print('safe_traceback='+str(n)+' frames='+','.join(frames[:30])+' types='+','.join(classes[:8]))
'''


def main():
    password = os.environ.get("SERVGO_PASSWORD")
    if not password:
        raise RuntimeError("Missing ServGo SSH secret")
    for attempt in range(3):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(Pinned())
        try:
            client.connect(HOST, port=22, username="root", password=password, look_for_keys=False,
                           allow_agent=False, timeout=12, banner_timeout=25, auth_timeout=15)
            break
        except Exception:
            client.close()
            if attempt == 2:
                raise RuntimeError("ServGo unavailable") from None
            time.sleep(3)
    try:
        _, out, _ = client.exec_command("/opt/booking_bot/venv/bin/python -c " + shlex.quote(REMOTE), timeout=22)
        result = out.read().decode("utf-8", "replace")
        if out.channel.recv_exit_status():
            raise RuntimeError("Safe bot diagnosis failed")
        print(result)
    finally:
        client.close()


if __name__ == "__main__":
    main()
