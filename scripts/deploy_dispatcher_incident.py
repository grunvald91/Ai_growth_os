"""Guarded one-time dispatcher incident release using the standard Growth deployer.

The server's deploy_growth.py checks for running tasks, validates base-image
dependencies, rebuilds the 3 containers, and automatically rolls back on
failure. No SSH host key or customer credentials are accepted from the repo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

import paramiko

HOST = "155.212.162.47"
PIN = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
HERE = Path(__file__).resolve().parents[1]


class Pinned(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if fingerprint != PIN:
            raise RuntimeError("Unexpected SSH fingerprint")
        client.get_host_keys().add(hostname, key.get_name(), key)


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=HERE)


def remote(client: paramiko.SSHClient, command: str, timeout: int = 30) -> str:
    _stdin, stdout, _stderr = client.exec_command(command, timeout=timeout)
    value = stdout.read().decode("utf-8", "replace").strip()
    if stdout.channel.recv_exit_status() != 0:
        raise RuntimeError("Remote preflight command failed")
    return value


def main() -> None:
    password = os.environ.get("SERVER_SSH_PASSWORD")
    if not password:
        raise RuntimeError("No SSH secret; refusing to deploy")
    sha = git("rev-parse", "HEAD").decode().strip()
    base = git("merge-base", "HEAD", "origin/main").decode().strip()
    main_head = git("rev-parse", "origin/main").decode().strip()
    if base != main_head:
        raise RuntimeError("main has advanced or the fix branch is out of date")
    changed = [x for x in git("diff", "--name-only", base, sha, "--", "app").decode().splitlines()
               if x.startswith("app/")]
    if not changed or any(not (HERE / path).is_file() for path in changed):
        raise RuntimeError("Unexpected changed source list")
    if "app/dispatcher/fast_routes.py" not in changed or "app/dispatcher/owner_views.py" not in changed:
        raise RuntimeError("Expected dispatcher incident changes are missing")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(Pinned())
    client.connect(HOST, port=22, username="root", password=password, timeout=12,
                   auth_timeout=15, banner_timeout=25, allow_agent=False, look_for_keys=False)
    try:
        current = remote(client, "systemctl is-active nginx.service")
        if current != "active":
            raise RuntimeError("Nginx unhealthy; refuse release")
        check = remote(client, "docker exec ai-growth-os-api-1 python -c " + shlex.quote(
            "from sqlalchemy import select, func; "
            "from app.db.session import SessionLocal; "
            "from app.models.domain import Task, TaskStatus; "
            "s=SessionLocal(); "
            "print(s.scalar(select(func.count()).select_from(Task).where(Task.status == TaskStatus.RUNNING))); "
            "s.close()"
        ), timeout=20)
        if check.strip() != "0":
            raise RuntimeError("Active Growth tasks; not safe to deploy")

        code = ("import hashlib,json,os; paths=" + repr(changed)
                + "; root='/app/'; print(json.dumps({p:"
                + "(hashlib.sha256(open(root+p,'rb').read()).hexdigest() if os.path.isfile(root+p) else None)"
                + " for p in paths}))")
        deployed = json.loads(remote(client, "docker exec ai-growth-os-api-1 python -c " +
                               shlex.quote(code), timeout=30))
        for path in changed:
            before = subprocess.run(["git", "show", f"{base}:{path}"],
                                    cwd=HERE, capture_output=True)
            expected = hashlib.sha256(before.stdout).hexdigest() if before.returncode == 0 else None
            if deployed.get(path) != expected:
                raise RuntimeError(f"Production source drift for {path}; refusing overwrite")
        print(f"preflight_ok=true changed_files={len(changed)} active_tasks=0", flush=True)

        archive = git("archive", "--format=tar.gz", sha,
                      "app", "alembic/versions", "pyproject.toml", "products")
        if not (0 < len(archive) < 200_000_000):
            raise RuntimeError("Unexpected source archive size")
        command = "python3 /opt/ai_growth_os/deploy/deploy_growth.py deploy " + shlex.quote(sha)
        stdin, stdout, stderr = client.exec_command(command, timeout=1100)
        stdin.write(archive)
        stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        exit_code = stdout.channel.recv_exit_status()
        print("deployment_log_tail:\n" + "\n".join(out.splitlines()[-28:]), flush=True)
        if exit_code or f"DEPLOY_OK {sha}" not in out:
            raise RuntimeError("Standard Growth deploy failed or rolled back; inspect action logs")

        try:
            runtime = remote(client, "docker exec ai-growth-os-api-1 python -c " + shlex.quote(
                "from app.dispatcher.fast_routes import quick_capability; "
                "from app.dispatcher.service import DECISION_TIMEOUT_SECONDS; "
                "assert quick_capability('Что порекомендуешь сегодня сделать для бизнеса?') == 'recommend_today'; "
                "assert quick_capability('Покажи последние готовые видео') == 'show_recent_videos'; "
                "assert DECISION_TIMEOUT_SECONDS == 25; print('dispatcher_runtime_ok')"),
                timeout=30)
            if runtime != "dispatcher_runtime_ok":
                raise RuntimeError("Runtime smoke mismatch")
            status = remote(client, "curl -ksS -o /dev/null -w '%{http_code}' --max-time 8 "
                            "--resolve growth.servgo.ru:443:127.0.0.1 https://growth.servgo.ru/",
                            timeout=12)
            if status not in ("200", "301", "302", "303", "401"):
                raise RuntimeError("Growth public HTTPS gateway unhealthy")
        except Exception:
            try:
                rollback = remote(client, "python3 /opt/ai_growth_os/deploy/deploy_growth.py rollback",
                                  timeout=600)
                print("post_deploy_rollback=" + ("ok" if "ROLLBACK_OK" in rollback else "unknown"),
                      flush=True)
            except Exception:
                print("post_deploy_rollback=FAILED; manual incident response required", flush=True)
            raise
        print(f"DISPATCHER_PRODUCTION_FIXED=true release_sha={sha} growth_https={status}", flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
