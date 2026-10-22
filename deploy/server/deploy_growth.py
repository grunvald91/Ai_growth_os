#!/usr/bin/env python3
"""Deploy one commit of AI Growth OS to production. Runs on the server as root.

Called by the restricted GitHub Actions key (see ssh-deploy-entry.sh) or by hand. The
repository is private, so the server never downloads it: the caller streams the commit as
a gzipped tar on stdin.
    git archive --format=tar.gz <sha> app alembic/versions pyproject.toml products \
        | ssh root@<host> deploy <sha>
    deploy_growth.py rollback            # back to the compose set before the last deploy

app/ and products/ are copied onto the configured base image. The base image carries the
Python dependencies and the database schema, so a commit whose dependencies the base image
does not satisfy, or whose Alembic migrations differ from it, is refused: build a new base image (and migrate)
first, then point deploy.json at it. api/worker/telegram are recreated with the overlay
environment from deploy.json; on failed health checks the previous set is restored.
Never prints secrets: production secrets stay in /opt/ai_growth_os/.env on the server.
"""
from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath

HOME = Path("/opt/ai_growth_os/deploy")
CONFIG = HOME / "deploy.json"          # base_image, overlay_env (non-secret flags)
LAST_GOOD = HOME / "last-good.json"    # compose files of the set that ran before the last deploy
RELEASES = Path("/opt/servgo-releases/github")
PROJECT = ["docker", "compose", "--project-name", "ai-growth-os", "--env-file", "/opt/ai_growth_os/.env"]
SERVICES = ["api", "worker", "telegram"]
CONTAINERS = [f"ai-growth-os-{service}-1" for service in SERVICES]
HEALTH = ["http://127.0.0.1:8091/health", "http://127.0.0.1:8091/studio/team"]
KEEP_RELEASES = 5
MAX_ARCHIVE_BYTES = 200_000_000
SOURCE_ROOTS = ("app", "products", "alembic", "pyproject.toml")
# Runs inside the base image with the commit's pyproject.toml on stdin: which of its
# dependencies the image does not satisfy, and which migrations the image carries.
BASE_FACTS = """
import importlib.metadata as md, json, pathlib, sys, tomllib
try:
    from packaging.requirements import Requirement
except ImportError:
    from pip._vendor.packaging.requirements import Requirement
unmet = []
for line in tomllib.loads(sys.stdin.read())["project"]["dependencies"]:
    req = Requirement(line)
    if req.marker and not req.marker.evaluate():
        continue
    try:
        version = md.version(req.name)
    except md.PackageNotFoundError:
        unmet.append(line + " (not installed)")
        continue
    if not req.specifier.contains(version, prereleases=True):
        unmet.append(line + " (installed " + version + ")")
versions = pathlib.Path("/app/alembic/versions")
print(json.dumps({"unmet": unmet, "migrations": sorted(p.name for p in versions.glob("*.py"))}))
"""


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def cap(args) -> str:
    return subprocess.check_output(args, text=True)


def log(message: str) -> None:
    print(message, flush=True)


def config() -> dict:
    data = json.loads(CONFIG.read_text())
    for key in ("base_image", "overlay_env"):
        if key not in data:
            raise SystemExit(f"deploy.json lacks {key}")
    return data


def status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def wait_healthy(tries: int = 60) -> None:
    for url in HEALTH:
        for _ in range(tries):
            if status(url) in (200, 401):  # Studio pages require login.
                break
            time.sleep(2)
        else:
            raise RuntimeError(f"health check failed: {url}")


def running_set() -> dict:
    labels = json.loads(cap(["docker", "inspect", CONTAINERS[0]]))[0]["Config"]["Labels"]
    return {"files": labels["com.docker.compose.project.config_files"].split(","),
            "cwd": labels["com.docker.compose.project.working_dir"]}


def busy_jobs() -> int:
    code = ("from sqlalchemy import select, func\nfrom app.db.session import SessionLocal\n"
            "from app.models.domain import Task, TaskStatus\n"
            "with SessionLocal() as s:\n print(s.scalar(select(func.count()).select_from(Task)"
            ".where(Task.status == TaskStatus.RUNNING)))")
    return int(cap(["docker", "exec", CONTAINERS[0], "python", "-c", code]).strip())


def unpack(payload: bytes, target: Path) -> None:
    """Extract the expected source roots of a `git archive` tarball; reject anything else."""
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        members = []
        for member in archive.getmembers():
            parts = PurePosixPath(member.name).parts
            if member.name == "pax_global_header" or not parts:
                continue
            if parts[0] not in SOURCE_ROOTS:
                raise RuntimeError(f"unexpected path in archive: {member.name}")
            members.append(member)
        archive.extractall(target, members=members, filter="data")
    if not (target / "app").is_dir():
        raise RuntimeError("archive has no app/ directory")


def incompatibilities(source: Path, base: dict) -> list[str]:
    """What the base image lacks for this commit; empty when only app code changed."""
    problems = []
    if base["unmet"]:
        problems.append("the base image lacks dependencies: " + ", ".join(base["unmet"]))
    versions = source / "alembic" / "versions"
    migrations = sorted(p.name for p in versions.glob("*.py")) if versions.is_dir() else []
    if migrations != base["migrations"]:
        added = sorted(set(migrations) - set(base["migrations"]))
        removed = sorted(set(base["migrations"]) - set(migrations))
        problems.append(f"alembic migrations differ from the base image (added {added}, removed {removed})")
    return problems


def base_facts(base_image: str, source: Path) -> dict:
    pyproject = source / "pyproject.toml"
    if not pyproject.is_file():
        raise RuntimeError("archive has no pyproject.toml")
    return json.loads(subprocess.run(
        ["docker", "run", "--rm", "-i", "--network", "none", "--entrypoint", "python", base_image,
         "-c", BASE_FACTS], input=pyproject.read_text(), capture_output=True, text=True, check=True).stdout)


def build(base_image: str, source: Path, image: str) -> None:
    name = "growth-deploy-build"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    run(["docker", "create", "--name", name, base_image, "true"], stdout=subprocess.DEVNULL)
    try:
        run(["docker", "cp", str(source / "app") + "/.", name + ":/app/app"])
        if (source / "products").is_dir():
            run(["docker", "cp", str(source / "products") + "/.", name + ":/app/products"])
        run(["docker", "commit", "--change",
             'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]', name, image],
            stdout=subprocess.DEVNULL)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    # Offline import check: no network, no database writes.
    run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image, "-c",
         "import app.main, app.team_worker, app.telegram_control_bot"], stdout=subprocess.DEVNULL)


def compose_up(files: list[str], cwd: str) -> None:
    command = PROJECT + sum((["-f", f] for f in files), [])
    run(command + ["config", "--quiet"], cwd=cwd)
    run(command + ["up", "-d", "--no-deps", "--no-build"] + SERVICES, cwd=cwd)


ENV_FILE = Path("/opt/ai_growth_os/.env")
SSO_HANDOFF = HOME / "servgo_sso.env"   # the same two values under ServGo's names, for ServGo's own deploy
SSO_KEYS = ("SERVGO_SSO_SECRET", "SERVGO_INTERNAL_KEY")
SSO_SERVGO_NAMES = {"SERVGO_SSO_SECRET": "STUDIO_SSO_SECRET", "SERVGO_INTERNAL_KEY": "STUDIO_INTERNAL_KEY"}
SSO_MIN_LENGTH = 32


def provision_sso_secrets(env_file: Path = ENV_FILE, handoff: Path = SSO_HANDOFF) -> list[str]:
    """Make sure the sign-in secrets for the ServGo admin exist; create missing ones, never print them.

    Both secrets are random, live only in the server's .env (mode kept) and in a root-only handoff file
    that ServGo's own deploy copies into its environment. Existing values are never changed, so a
    repeated deploy does not log anyone out. Returns what was created (names only).
    """
    text = env_file.read_text() if env_file.exists() else ""
    values = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in SSO_KEYS:
            values[key.strip()] = value.strip().strip("\"'")
    created = []
    addition = ""
    for key in SSO_KEYS:
        if len(values.get(key, "")) < SSO_MIN_LENGTH:
            values[key] = secrets.token_hex(32)
            addition += f"{key}={values[key]}\n"
            created.append(key)
    if addition:
        # Short or empty placeholders (KEY=) are replaced, not duplicated.
        kept = [line for line in text.splitlines() if line.partition("=")[0].strip() not in created]
        body = "\n".join(kept) + ("\n" if kept else "")
        mode = env_file.stat().st_mode & 0o777 if env_file.exists() else 0o600
        descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(body + addition)
    handoff.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(handoff, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(f"{SSO_SERVGO_NAMES[key]}={values[key]}\n" for key in SSO_KEYS))
    os.chmod(handoff, 0o600)
    return created


def deploy(sha: str, payload: bytes) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("commit sha must be 40 lowercase hex characters")
    if not payload:
        raise SystemExit("no source archive on stdin (pipe `git archive --format=tar.gz <sha> ...`)")
    if len(payload) > MAX_ARCHIVE_BYTES:
        raise SystemExit("source archive is too large")
    settings = config()
    busy = busy_jobs()
    if busy:
        raise SystemExit(f"{busy} agent task(s) running; deploy later so they are not cut off")

    previous = running_set()
    release = RELEASES / sha
    if any(str(release) in f for f in previous["files"]):
        raise SystemExit(f"{sha[:12]} is already running")
    shutil.rmtree(release, ignore_errors=True)
    unpack(payload, release)
    problems = incompatibilities(release, base_facts(settings["base_image"], release))
    if problems:
        shutil.rmtree(release, ignore_errors=True)
        raise SystemExit("refusing to deploy onto " + settings["base_image"] + ": " + "; ".join(problems)
                         + ". Build a new base image (and migrate) first, then update deploy.json.")
    image = f"servgo-growth:{sha[:12]}"
    log(f"building {image} on {settings['base_image']}")
    build(settings["base_image"], release, image)

    base_files = [f for f in previous["files"] if not f.startswith("/opt/servgo-releases/")]
    overlay = release / "overlay.json"
    overlay.write_text(json.dumps({"services": {s: {"image": image, "environment": {
        **settings["overlay_env"], "RELEASE_SHA": sha}} for s in SERVICES}}, indent=2))
    LAST_GOOD.write_text(json.dumps(previous))
    created = provision_sso_secrets()
    log("ServGo sign-in secrets: " + (("created " + ", ".join(created)) if created else "already present"))
    try:
        compose_up(base_files + [str(overlay)], previous["cwd"])
        wait_healthy()
        time.sleep(10)
        for container in CONTAINERS:
            state = json.loads(cap(["docker", "inspect", container]))[0]["State"]
            if not state["Running"] or state["Restarting"]:
                raise RuntimeError(container + " is not running")
    except Exception as exc:
        log(f"DEPLOY_FAILED ({type(exc).__name__}: {exc}); restoring the previous set")
        rollback()
        raise SystemExit(1)
    cleanup(keep=[Path(f).parent for f in previous["files"]])
    log(f"DEPLOY_OK {sha}")


def rollback() -> None:
    if not LAST_GOOD.exists():
        raise SystemExit("nothing to roll back to: no previous deploy recorded")
    previous = json.loads(LAST_GOOD.read_text())
    compose_up(previous["files"], previous["cwd"])
    wait_healthy()
    log("ROLLBACK_OK")


def cleanup(keep: list[Path]) -> None:
    """Drop old release sources, but never the one the rollback set points to."""
    releases = sorted((p for p in RELEASES.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
    for old in releases[:-KEEP_RELEASES]:
        if old not in keep:
            shutil.rmtree(old, ignore_errors=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["deploy"] and len(args) == 2:
        deploy(args[1], sys.stdin.buffer.read(MAX_ARCHIVE_BYTES + 1))
    elif args == ["rollback"]:
        rollback()
    else:
        raise SystemExit("usage: deploy_growth.py deploy <sha> < source.tar.gz | rollback")
