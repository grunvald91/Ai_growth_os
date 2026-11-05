"""Manual, scoped SSH SOCKS relay setup. Never print credentials or remote command stderr."""
import base64
import hashlib
import logging
import os
import shlex
import stat
import time

import paramiko

SOURCE = "155.212.162.47"
RELAY = "89.125.90.101"
SOURCE_FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
RELAY_FP = "+SqoDNS4YTI9OKHifsEpbLwWof6oXONbdFX+GLSTlC0"
KEY_FILE = "/root/.ssh/servgo_89_ed25519"
KNOWN_HOSTS = "/etc/booking-bot-relay89/known_hosts"
UNIT = "/etc/systemd/system/booking-bot-relay89.service"
logging.getLogger("paramiko").setLevel(logging.CRITICAL)


class PinnedKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, expected):
        self.expected = expected

    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if key.get_name() != "ssh-ed25519" or fingerprint != self.expected:
            raise paramiko.SSHException("Pinned SSH host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def connect(host, password, fingerprint, retries=1):
    for attempt in range(retries):
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedKey(fingerprint))
        try:
            client.connect(host, port=22, username="root", password=password,
                           look_for_keys=False, allow_agent=False,
                           timeout=12, banner_timeout=25, auth_timeout=15)
            return client
        except Exception:
            client.close()
            if attempt + 1 == retries:
                raise RuntimeError(f"Connection to approved host {host} failed") from None
            time.sleep(3)


def remote(client, command, timeout=30):
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    text = stdout.read().decode("utf-8", "replace")
    if stdout.channel.recv_exit_status():
        raise RuntimeError("Remote command failed (stderr suppressed)")
    return text.strip()


def python_remote(client, code, *args, timeout=30):
    command = "python3 -c " + shlex.quote(code)
    for arg in args:
        command += " " + shlex.quote(arg)
    return remote(client, command, timeout)


def ensure_file(client, path, expected, mode):
    sftp = client.open_sftp()
    try:
        try:
            with sftp.open(path, "rb") as f:
                current = f.read().decode("utf-8")
        except FileNotFoundError:
            current = None
        if current is not None:
            if current != expected:
                raise RuntimeError(f"Unexpected existing file at {path}; refusing overwrite")
            return
        temp = path + ".tmp"
        try:
            leftover = sftp.lstat(temp)
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(leftover.st_mode) or leftover.st_size != 0:
                raise RuntimeError(f"Unexpected nonempty or nonregular temporary file at {temp}")
            sftp.remove(temp)
        with sftp.open(temp, "wx") as f:
            f.write(expected.encode())
        sftp.chmod(temp, mode)
        sftp.rename(temp, path)
    finally:
        sftp.close()


def main():
    source_password = os.environ.get("SERVGO_PASSWORD")
    relay_password = os.environ.get("RELAY_PASSWORD")
    if not source_password or not relay_password:
        raise RuntimeError("Required GitHub secrets are missing")
    # Relay login once, source login with transient network retries. Neither touches 188.
    source = connect(SOURCE, source_password, SOURCE_FP, retries=3)
    try:
        route = remote(source, "ip -4 route get 89.125.90.101")
        if " src " + SOURCE + " " not in " " + route + " ":
            raise RuntimeError("ServGo source address is not the allowed SSH key source")
        key = remote(source, "cat /root/.ssh/servgo_89_ed25519.pub")
        parts = key.split()
        if len(parts) != 3 or parts[0] != "ssh-ed25519":
            raise RuntimeError("Dedicated ServGo public key missing or invalid")
        base64.b64decode(parts[1], validate=True)
        relay = connect(RELAY, relay_password, RELAY_FP)
        try:
            settings = remote(relay, "/usr/sbin/sshd -T -C user=root,host=89.125.90.101,addr=155.212.162.47")
            if "allowtcpforwarding yes" not in settings or "pubkeyauthentication yes" not in settings:
                raise RuntimeError("Relay disallows SSH key TCP forwarding")
            # Root key restricted to the source address and Telegram Bot API only.
            line = f'from="{SOURCE}",restrict,port-forwarding,permitopen="api.telegram.org:443" {key}\n'
            install = '''import os,pathlib,sys
path=pathlib.Path('/root/.ssh/authorized_keys')
if path.is_symlink(): raise RuntimeError('authorized_keys must not be a symlink')
path.parent.mkdir(mode=0o700,exist_ok=True)
current=path.read_text() if path.exists() else ''
blob=sys.argv[1].split()[-2]
if blob in current and sys.argv[1].strip() not in current.splitlines():
    raise RuntimeError('key exists with unexpected restrictions')
if sys.argv[1].strip() not in current.splitlines():
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600),'w') as f:
        f.write(('\\n' if current and not current.endswith('\\n') else '')+sys.argv[1]); f.flush(); os.fsync(f.fileno())
os.chmod(path.parent,0o700)
os.chmod(path,0o600)
print('restricted_key_installed=yes')'''
            print(python_remote(relay, install, line))
            host_key = relay.get_transport().get_remote_server_key()
            host_line = f"{RELAY} {host_key.get_name()} {host_key.get_base64()}\n"
        finally:
            relay.close()
        remote(source, "install -d -m 755 /etc/booking-bot-relay89")
        ensure_file(source, KNOWN_HOSTS, host_line, 0o644)
        service = f"""[Unit]
Description=Private SSH SOCKS egress to relay 89 for booking bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -D 127.0.0.1:1089 -i {KEY_FILE} -o BatchMode=yes -o IdentitiesOnly=yes -o PreferredAuthentications=publickey -o PasswordAuthentication=no -o StrictHostKeyChecking=yes -o UserKnownHostsFile={KNOWN_HOSTS} -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 root@{RELAY}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
"""
        ensure_file(source, UNIT, service, 0o644)
        remote(source, "systemctl daemon-reload && systemctl enable --now booking-bot-relay89.service", timeout=25)
        # curl must use the tunnel and resolve api.telegram.org on relay 89, not locally.
        probe = "curl -4 -sS --proxy socks5h://127.0.0.1:1089 --noproxy '' --connect-timeout 6 --max-time 15 -o /dev/null -w '%{http_code}' https://api.telegram.org/"
        ok = False
        for _ in range(3):
            try:
                result = remote(source, probe, timeout=20)
                if result == "302":
                    ok = True
                    break
            except RuntimeError:
                pass
            time.sleep(3)
        if not ok:
            remote(source, "systemctl disable --now booking-bot-relay89.service", timeout=20)
            raise RuntimeError("SOCKS-to-Telegram probe failed; new tunnel stopped")
        print("relay_tunnel_active=" + remote(source, "systemctl is-active booking-bot-relay89.service"))
        print("bot_api_over_relay_http=" + result)
        print("production_bot_unchanged=yes")
    finally:
        source.close()


if __name__ == "__main__":
    main()
