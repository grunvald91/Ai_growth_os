#!/usr/bin/env python3
"""One-time preparation of the production server for the ServGo sign-in / dispatcher release. Runs as root.

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
To undo by hand: restore the .bak files and, if wanted, `alembic downgrade 0006` from the new image.
Never prints secrets: the database URL is passed to docker without being logged.
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path("/opt/ai_growth_os/deploy")
CONFIG = HOME / "deploy.json"
INSTALLED = HOME / "deploy_growth.py"
API = "ai-growth-os-api-1"
NEW_TAG = "servgo-growth-release:20261023"


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
    problems = plan(facts["migrations"], new_migrations, current, facts["unmet"])
    log(f"base_image={base} database_revision={current} new_migrations="
        f"{sorted(set(new_migrations) - set(facts['migrations']))}")
    if problems:
        raise SystemExit("not preparing: " + "; ".join(problems))
    if (set(new_migrations) == set(facts["migrations"])):
        log("base image already carries every migration; nothing to build")
        image = base
    else:
        name = "growth-prepare-build"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        run(["docker", "create", "--name", name, base, "true"], stdout=subprocess.DEVNULL)
        try:
            run(["docker", "cp", str(release / "alembic/versions") + "/.", name + ":/app/alembic/versions"])
            run(["docker", "commit", "--change", 'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
                 name, NEW_TAG], stdout=subprocess.DEVNULL)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        image = NEW_TAG
        log(f"built {image}")
    heads = alembic(image, "heads").split()
    log(f"image_head={heads[0] if heads else None}")
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
