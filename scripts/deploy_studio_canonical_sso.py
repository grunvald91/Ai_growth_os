"""Safe, focused deployment of canonical Studio SSO ticket origin.

No schema changes, no mass replacement of the divergent production web/api.
Refuses unexpected live function shapes. Backs up the existing file, updates
only the reviewed origin selection, verifies syntax and service health, and
rolls back on any failed post-write check. Never prints credentials or tickets.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import os
from pathlib import Path
import shlex
import stat
import time

import paramiko


HOST = "155.212.162.47"
HOST_KEY_SHA256 = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
REMOTE = "/opt/booking_bot/web/api/routes/studio_sso.py"
SERVICE = "booking-web-api.service"
LOCAL = Path(__file__).resolve().parents[1] / "web/api/routes/studio_sso.py"


def _function(source: str) -> ast.FunctionDef:
    tree = ast.parse(source)
    found = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name == "studio_public_url"]
    if len(found) != 1 or found[0].decorator_list or found[0].args.args:
        raise ValueError("Unexpected Studio SSO entrypoint")
    return found[0]


def _canonical_constant(source: str) -> bool:
    tree = ast.parse(source)
    return any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "CANONICAL_STUDIO_URL"
                for t in node.targets)
        and isinstance(node.value, ast.Constant)
        and node.value.value == "https://servgo.ru"
        for node in tree.body
    )


def patch(source: str, target: str) -> tuple[str, bool]:
    """Replace only the reviewed function; preserve every other remote line."""
    if not _canonical_constant(source) or not _canonical_constant(target):
        raise ValueError("Canonical SSO origin is not the expected host")
    live, desired = _function(source), _function(target)
    if len(desired.body) != 2 or not isinstance(desired.body[1], ast.Return):
        raise ValueError("Unexpected proposed Studio SSO function")
    if not isinstance(desired.body[1].value, ast.Name) or desired.body[1].value.id != "CANONICAL_STUDIO_URL":
        raise ValueError("Proposed Studio SSO must return fixed canonical origin")
    existing_body = live.body[-1]
    if isinstance(existing_body, ast.Return) and isinstance(existing_body.value, ast.Name):
        if existing_body.value.id == "CANONICAL_STUDIO_URL":
            return source, False
    old_return = isinstance(existing_body, ast.Return) and isinstance(existing_body.value, ast.IfExp)
    if not old_return or ast.unparse(existing_body.value) != "CANONICAL_STUDIO_URL if enabled else STUDIO_URL":
        raise ValueError("Unexpected live Studio SSO routing; refusing to overwrite")
    if len(live.body) != 3 or not isinstance(live.body[0], ast.Expr) or not isinstance(live.body[1], ast.Assign):
        raise ValueError("Unexpected live Studio SSO function statements")
    assigned = live.body[1].targets
    if len(assigned) != 1 or not isinstance(assigned[0], ast.Name) or assigned[0].id != "enabled":
        raise ValueError("Unexpected live Studio gateway state")
    desired_function = ast.get_source_segment(target, desired)
    if not desired_function:
        raise ValueError("Unable to extract reviewed canonical entrypoint")
    lines = source.splitlines(keepends=True)
    start, end = live.lineno - 1, live.end_lineno
    result = "".join(lines[:start]) + desired_function + "\n" + "".join(lines[end:])
    checked = _function(result)
    if not isinstance(checked.body[-1], ast.Return) or not isinstance(checked.body[-1].value, ast.Name):
        raise ValueError("Patched Studio SSO is not canonical")
    if checked.body[-1].value.id != "CANONICAL_STUDIO_URL":
        raise ValueError("Patched Studio SSO returned an incorrect constant")
    return result, True


class PinnedHost(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != HOST_KEY_SHA256:
            raise paramiko.SSHException("Unexpected production SSH host key")
        client.get_host_keys().add(hostname, key.get_name(), key)


def run(client, command: str, timeout: int = 60) -> str:
    _, stdout, _ = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError("Production verification failed; stderr intentionally hidden")
    return output


def wait_for_api_health(client, *, attempts: int = 20, delay_seconds: float = 2) -> None:
    """Wait for a successful API response; rollback caller handles exhaustion."""
    for attempt in range(attempts):
        try:
            health = run(client, "curl -s -o /dev/null -w '%{http_code}' "
                         "--max-time 3 http://127.0.0.1:8000/health", timeout=8)
        except RuntimeError:
            health = ""
        if health == "200":
            return
        if attempt + 1 < attempts:
            time.sleep(delay_seconds)
    raise RuntimeError("ServGo API did not become healthy after restart")


def release() -> None:
    password = os.environ.get("SERVGO_PASSWORD", "")
    if not password:
        raise RuntimeError("Production SSH credential not configured")
    target = LOCAL.read_text(encoding="utf-8")
    if not _canonical_constant(target):
        raise ValueError("Repository SSO code does not point to canonical ServGo")
    remote_q = shlex.quote(REMOTE)
    service_q = shlex.quote(SERVICE)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHost())
    client.connect(HOST, username="root", password=password, port=22,
                   allow_agent=False, look_for_keys=False, timeout=15,
                   banner_timeout=30, auth_timeout=15)
    sftp = client.open_sftp()
    staging = REMOTE + ".canonical-staging-" + str(os.getpid())
    backup = REMOTE + ".canonical-backup-" + str(int(time.time()))
    replaced = False
    try:
        with sftp.open(REMOTE, "rb") as handle:
            live_bytes = handle.read()
        live = live_bytes.decode("utf-8")
        revised, changed = patch(live, target)
        if not changed:
            print("STUDIO_CANONICAL_SSO=ALREADY_ACTIVE")
            return
        if os.environ.get("APPLY") != "true":
            print("STUDIO_CANONICAL_SSO=PREFLIGHT_OK")
            return
        with sftp.open(staging, "wb") as handle:
            handle.write(revised.encode("utf-8"))
        mode = stat.S_IMODE(sftp.stat(REMOTE).st_mode)
        run(client, "python3 -m py_compile " + shlex.quote(staging), timeout=15)
        run(client, "cp -p -- " + remote_q + " " + shlex.quote(backup))
        run(client, "install -m " + format(mode, "o") + " -- " +
            shlex.quote(staging) + " " + remote_q)
        replaced = True
        run(client, "python3 -m py_compile " + remote_q, timeout=15)
        run(client, "systemctl restart " + service_q, timeout=70)
        if run(client, "systemctl is-active " + service_q) != "active":
            raise RuntimeError("ServGo API is not active")
        # systemctl "active" can precede uvicorn readiness after a cold start.
        wait_for_api_health(client)
        # Verify the actual server file still returns only the approved origin.
        with sftp.open(REMOTE, "rb") as handle:
            active = handle.read().decode("utf-8")
        _, needs_patch = patch(active, target)
        if needs_patch:
            raise RuntimeError("Canonical Studio SSO failed post-release verification")
        print("STUDIO_CANONICAL_SSO=DEPLOYED;API_HEALTH=200")
    except Exception:
        if replaced:
            try:
                run(client, "cp -p -- " + shlex.quote(backup) + " " + remote_q)
                run(client, "systemctl restart " + service_q, timeout=70)
                print("STUDIO_CANONICAL_SSO=ROLLED_BACK")
            except Exception:
                print("STUDIO_CANONICAL_SSO=ROLLBACK_FAILED_MANUAL_CHECK_REQUIRED")
        raise
    finally:
        try:
            sftp.remove(staging)
        except IOError:
            pass
        sftp.close()
        client.close()


if __name__ == "__main__":
    release()
