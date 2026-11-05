"""One-time, fail-closed Yandex Mail release. Only two existing Mail files.

Every target is compared with the known pre-release git blob SHA; server drift
aborts before writing anything. Backups and rollback protect production.
No GitHub secret or company data is printed.
"""
from __future__ import annotations

import base64
import hashlib
import os
import shlex
import stat
import time
from pathlib import Path

import paramiko

HOST = "155.212.162.47"
HOST_KEY_FINGERPRINT = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot"
SERVICE = "booking-web-api.service"
TARGETS = {
    "web/api/routes/mail_company_search.py": "8801d9632e901da0077085e42cb48d29f9e6254d",
    "web/api/routes/mail_campaign_dashboard.html": "f43d2a530e334367222a2889d5d8d1c4c677af4f",
}


def blob_sha(payload: bytes) -> str:
    """Git's SHA-1 blob identity; compare with the unchanged main file blob."""
    prefix = b"blob " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(prefix + payload).hexdigest()


def decision(actual: bytes, expected_old_blob: str, intended: bytes) -> str:
    if actual == intended:
        return "already_current"
    return "replace" if blob_sha(actual) == expected_old_blob else "drift"


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != HOST_KEY_FINGERPRINT:
            raise paramiko.SSHException("Production SSH host fingerprint mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def run(ssh, command: str, timeout: int = 90) -> str:
    _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
    result = stdout.read().decode("utf-8", errors="replace").strip()
    if stdout.channel.recv_exit_status():
        # Never print arbitrary process stderr: it may contain private data.
        raise RuntimeError("Production command failed")
    return result


def check_http(ssh, path: str) -> str:
    return run(ssh, "curl -sS --max-time 8 -o /dev/null -w '%{http_code}' "
               + shlex.quote("http://127.0.0.1:8000" + path) + " || true")


def load_regular(sftp, path: str) -> bytes:
    info = sftp.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("Expected regular file, refusing symlink or special file")
    with sftp.open(path, "rb") as file:
        return file.read()


def upload_atomic(sftp, path: str, payload: bytes, mode: int) -> None:
    tmp = path + ".yandex-mail-release.tmp"
    try:
        sftp.remove(tmp)
    except OSError:
        pass
    try:
        with sftp.open(tmp, "wx") as target:
            target.write(payload)
        sftp.chmod(tmp, stat.S_IMODE(mode))
        sftp.posix_rename(tmp, path)
    finally:
        try:
            sftp.remove(tmp)
        except OSError:
            pass


def connect():
    password = os.getenv("SERVGO_PASSWORD", "")
    if not password:
        raise RuntimeError("The authorized production SSH secret is unavailable")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHostKey())
    try:
        client.connect(HOST, username="root", port=22, password=password,
                       look_for_keys=False, allow_agent=False,
                       timeout=15, banner_timeout=20, auth_timeout=20)
    except Exception:
        client.close()
        raise RuntimeError("Unable to authenticate the pinned production host") from None
    return client


def main() -> None:
    apply = os.getenv("APPLY", "false").lower() == "true"
    candidate = {name: Path(name).read_bytes() for name in TARGETS}
    compile(candidate["web/api/routes/mail_company_search.py"], "mail_company_search.py", "exec")
    ssh = connect()
    sftp = ssh.open_sftp()
    backups = {}
    changed = False
    try:
        if run(ssh, "systemctl is-active " + SERVICE) != "active":
            raise RuntimeError("Mail API service is not active")
        if check_http(ssh, "/health") != "200":
            raise RuntimeError("Mail API failed pre-deployment health")
        plan = {}
        for rel, known_old in TARGETS.items():
            remote = ROOT + "/" + rel
            existing = load_regular(sftp, remote)
            action = decision(existing, known_old, candidate[rel])
            plan[rel] = (action, sftp.lstat(remote).st_mode)
            print(rel + ": " + action)
        if any(action == "drift" for action, _ in plan.values()):
            raise RuntimeError("Server differs from the reviewed main version; production untouched")
        if not apply:
            print("READ_ONLY_PREFLIGHT_OK")
            return
        todo = [rel for rel, (action, _) in plan.items() if action == "replace"]
        if not todo:
            print("MAIL_YANDEX_ALREADY_RELEASED")
            return
        base = ROOT + "/.mail-yandex-backup-" + str(int(time.time()))
        run(ssh, "install -d -m 700 " + shlex.quote(base))
        for rel in todo:
            remote = ROOT + "/" + rel
            backup = base + "/" + rel.rsplit("/", 1)[-1]
            run(ssh, "cp -p " + shlex.quote(remote) + " " + shlex.quote(backup))
            backups[remote] = backup
        # Rollback from this point on, including partial transfer failures.
        changed = True
        for rel in todo:
            upload_atomic(sftp, ROOT + "/" + rel, candidate[rel], plan[rel][1])
        run(ssh, "cd " + shlex.quote(ROOT)
            + " && ./venv/bin/python -m py_compile web/api/routes/mail_company_search.py")
        run(ssh, "systemctl restart " + SERVICE)
        for attempt in range(20):
            if check_http(ssh, "/health") == "200":
                break
            time.sleep(2)
        else:
            raise RuntimeError("Mail API did not recover after release")
        if check_http(ssh, "/owner-mail/api/company-search/providers") not in ("401", "403"):
            raise RuntimeError("Mail search route is not protected or unavailable")
        for rel in todo:
            if load_regular(sftp, ROOT + "/" + rel) != candidate[rel]:
                raise RuntimeError("File verification failed after release")
        print("MAIL_YANDEX_RELEASE_OK")
    except Exception:
        if changed:
            try:
                for remote, backup in backups.items():
                    run(ssh, "cp -p " + shlex.quote(backup) + " " + shlex.quote(remote))
                run(ssh, "systemctl restart " + SERVICE)
                print("MAIL_YANDEX_ROLLBACK_COMPLETE")
            except Exception:
                print("MAIL_YANDEX_ROLLBACK_INCOMPLETE")
        raise
    finally:
        sftp.close()
        ssh.close()


if __name__ == "__main__":
    main()
