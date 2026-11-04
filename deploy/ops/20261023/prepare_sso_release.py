#!/usr/bin/env python3
"""Preparation of the production server for a release that adds database migrations. Runs as root.

First used for the ServGo sign-in / dispatcher release (migrations 0007-0008), and again for any later release
with new migrations: the new base image is tagged with the head revision, so earlier base images stay for rollback.

    prepare_sso_release.py <release-dir>

<release-dir> holds `alembic/`, `pyproject.toml` and `deploy/server/deploy_growth.py` of the commit to deploy
(the workflow extracts `git archive` there). Everything is checked before anything is changed:
  1. the running api container's database is at the revision the current base image expects;
  2. the new migrations only follow it (no gaps), and the base image lacks no dependency;
Then, in order:
  3. a new base image = current base image + the commit's alembic/versions (nothing else changes in it);
  4. `alembic upgrade head` from that image against the production database (new tables only, additive);
  5. deploy.json points to the new base image; the server's deploy_growth.py is replaced (both backed up).
A failure before step 4 changes nothing important; after it the new tables simply stay (harmless).
To undo by hand: restore the .bak files and, if wanted, `alembic downgrade <previous revision>` from the new image.
Never prints secrets: the database URL is passed to docker without being logged.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HOME = Path("/opt/ai_growth_os/deploy")
CONFIG = HOME / "deploy.json"
INSTALLED = HOME / "deploy_growth.py"
API = "ai-growth-os-api-1"
TAG_PREFIX = "servgo-growth-release:"
POSTGRES = "ai-growth-os-postgres-1"
BACKUPS = HOME.parent / "backups"
# Only the dependency for Brand Kit's bundled WOFF2 font is approved for
# an in-place extension of the otherwise immutable production base image.
ALLOWED_INSTALL = frozenset({"fonttools[woff]>=4.60,<5"})


def run(args, **kw):
    return subprocess.run(args, check=True, **kw)


def cap(args, **kw) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout


def log(message: str) -> None:
    print(message, flush=True)


def revision_of(output: str) -> str | None:
    """The revision id out of `alembic current` output ('0006 (head)'), None when the database has none."""
    found = re.findall(r"^([0-9a-z_]+)(?: \(head\))?\s*$", output, flags=re.MULTILINE)
    return found[-1] if found else None


def plan(base_migrations: list[str], new_migrations: list[str], current: str | None, unmet: list[str]) -> list[str]:
    """Problems that must stop the preparation; empty means go."""
    problems = []
    if unmet:
        problems.append("the base image lacks dependencies: " + ", ".join(unmet))
    if not set(base_migrations) <= set(new_migrations):
        problems.append("the commit lacks migrations the base image has: "
                        + ", ".join(sorted(set(base_migrations) - set(new_migrations))))
    expected = max((name.split("_")[0] for name in base_migrations), default=None)
    if current != expected:
        problems.append(f"the database is at {current}, the current base image expects {expected}")
    return problems


def installable_requirements(release: Path, unmet: list[str]) -> list[str]:
    """Fail closed on arbitrary missing packages; allow the reviewed font extra only."""
    project = tomllib.loads((release / "pyproject.toml").read_text())
    declared = project["project"]["dependencies"]
    candidates = [item for item in declared if item in ALLOWED_INSTALL]
    return [item for item in candidates if any(error.startswith(item + " (") for error in unmet)]


def backup_database(current: str | None, newest: str) -> Path:
    """A verified PostgreSQL custom-format dump before Alembic touches the database."""
    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = f"before-brand-kit-{current or 'unknown'}-to-{newest}-{int(time.time())}.dump"
    path = BACKUPS / name
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            subprocess.run(
                ["docker", "exec", POSTGRES, "pg_dump", "-U", "ai_growth",
                 "-d", "ai_growth", "-Fc"],
                stdout=stream, stderr=subprocess.PIPE, check=True)
            stream.flush()
            os.fsync(stream.fileno())
        with path.open("rb") as saved:
            if saved.read(5) != b"PGDMP":
                raise RuntimeError("PostgreSQL backup is not a custom-format dump")
        if path.stat().st_size < 100:
            raise RuntimeError("PostgreSQL backup appears empty")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    log(f"PG_BACKUP_OK {path.name} bytes={path.stat().st_size}")
    return path


def release_tag(migrations: list[str]) -> str:
    """The base image tag of a release: named after its newest migration, so older images are never overwritten."""
    return TAG_PREFIX + max(name.split("_")[0] for name in migrations)


def load_deploy_module(release: Path):
    spec = importlib.util.spec_from_file_location("new_deploy_growth", release / "deploy/server/deploy_growth.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def database_env() -> list[str]:
    """`-e DATABASE_URL=...` for a one-off container, taken from the running api container (never printed)."""
    env = json.loads(cap(["docker", "inspect", API]))[0]["Config"]["Env"]
    urls = [item for item in env if item.startswith("DATABASE_URL=")]
    if len(urls) != 1:
        raise RuntimeError("cannot find the database setting of the running api container")
    return ["-e", urls[0]]


def alembic(image: str, *args: str) -> str:
    # Same network namespace as the api container, so the database host name resolves exactly as it does there.
    return cap(["docker", "run", "--rm", "--network", f"container:{API}", *database_env(), "--entrypoint", "alembic",
                image, *args])


def main(release: Path) -> None:
    config = json.loads(CONFIG.read_text())
    base = config["base_image"]
    module = load_deploy_module(release)
    facts = module.base_facts(base, release)
    new_migrations = sorted(p.name for p in (release / "alembic/versions").glob("*.py"))
    current = revision_of(alembic(base, "current"))
    allowed = installable_requirements(release, facts["unmet"])
    unsupported = [item for item in facts["unmet"]
                   if not any(item.startswith(req + " (") for req in allowed)]
    problems = plan(facts["migrations"], new_migrations, current, unsupported)
    log(f"base_image={base} database_revision={current} new_migrations="
        f"{sorted(set(new_migrations) - set(facts['migrations']))}")
    if problems:
        raise SystemExit("not preparing: " + "; ".join(problems))
    changed = set(new_migrations) != set(facts["migrations"])
    if not changed and not allowed:
        log("base image already carries every migration and dependency")
        image = base
    else:
        name = "growth-prepare-build"
        image = release_tag(new_migrations) + ("-fonttools" if allowed else "")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        if allowed:
            command = "python -m pip install --no-cache-dir " + " ".join(
                shlex.quote(req) for req in allowed)
            run(["docker", "create", "--name", name, base, "sh", "-lc", command],
                stdout=subprocess.DEVNULL)
        else:
            run(["docker", "create", "--name", name, base, "true"],
                stdout=subprocess.DEVNULL)
        try:
            if allowed:
                run(["docker", "start", name], stdout=subprocess.DEVNULL)
                exit_code = cap(["docker", "wait", name]).strip()
                if exit_code != "0":
                    raise RuntimeError("Installing reviewed fontTools dependency failed")
                log("FONTTOOLS_INSTALL_OK")
            run(["docker", "cp", str(release / "alembic/versions") + "/.",
                 name + ":/app/alembic/versions"])
            # Check package versions against the release's manifest in the final
            # container, not just pip's exit code.
            run(["docker", "commit", "--change", 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
                 name, image], stdout=subprocess.DEVNULL)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        final = module.base_facts(image, release)
        if final["unmet"] or sorted(final["migrations"]) != new_migrations:
            raise RuntimeError("Prepared base does not match required dependencies/migrations")
        log(f"PREPARED_IMAGE_OK {image}")
    heads = alembic(image, "heads").split()
    log(f"image_head={heads[0] if heads else None}")
    if changed:
        backup_database(current, max(name.split("_")[0] for name in new_migrations))
    alembic(image, "upgrade", "head")
    after = revision_of(alembic(image, "current"))
    log(f"database_revision_after={after}")
    if after != heads[0]:
        raise RuntimeError("the database is not at the image head after the upgrade")

    stamp = str(int(time.time()))
    shutil.copy2(CONFIG, f"{CONFIG}.bak.{stamp}")
    config["base_image"] = image
    CONFIG.write_text(json.dumps(config, indent=2) + "\n")
    if INSTALLED.exists():
        shutil.copy2(INSTALLED, f"{INSTALLED}.bak.{stamp}")
    shutil.copy2(release / "deploy/server/deploy_growth.py", INSTALLED)
    INSTALLED.chmod(0o700)
    log(f"deploy.json now points to {image}; deploy_growth.py replaced (backups *.bak.{stamp})")
    log("PREPARE_OK")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: prepare_sso_release.py <release-dir>")
    main(Path(sys.argv[1]))
