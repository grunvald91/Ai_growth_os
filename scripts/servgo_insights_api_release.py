"""Guarded, scoped ServGo booking analytics backend rollout.

Never deploys on a push. Requires workflow_dispatch and APPLY=true, with
production environment approval. It refuses unknown live file content and
backs up each affected file before replacing it. No client data is printed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import time

from scripts.deploy_business_creation_and_reminders import (
    ROOT, WEB_SERVICE, connect, put, run, wait_web_ready,
)

FILES = (
    "web/api/routes/studio_sso.py",
    "web/api/routes/studio_schedule.py",
    "web/api/routes/admin.py",
    "web/api/routes/crm.py",
)
BACKUPS = ROOT + "/.servgo-insights-api-releases"


def digest(data: bytes | None) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else "missing"


def classify(current: bytes | None, desired: bytes, known_hashes: set[str]) -> str:
    """Fail closed for unknown server changes, never infer an absent file is safe."""
    if current == desired:
        return "already"
    if current is None or digest(current) not in known_hashes:
        return "conflict"
    return "replace"


def accepted_versions(repo: Path, path: str) -> set[str]:
    """Only source blobs recorded in current main's ancestry are safe baselines."""
    commits = subprocess.check_output(
        ["git", "log", "-n", "400", "--format=%H", "HEAD", "--", path],
        cwd=repo, text=True,
    ).splitlines()
    if not commits:
        raise RuntimeError(f"No Git history for {path}")
    hashes = set()
    for commit in commits:
        result = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=repo, capture_output=True,
        )
        if result.returncode == 0:
            hashes.add(digest(result.stdout))
    return hashes


def read_remote(sftp, path: str) -> bytes | None:
    try:
        with sftp.open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None



def _check_live_contract(spec: dict) -> None:
    """Require the exact added API contract; /health alone cannot prove rollout."""
    paths = spec.get("paths", {})
    if "/internal/studio/businesses" not in paths:
        raise RuntimeError("The live API does not expose the Studio business list")
    schedule = paths.get("/internal/studio/schedule", {}).get("get", {})
    parameters = {part.get("name") for part in schedule.get("parameters", [])}
    if "slot_service_id" not in parameters:
        raise RuntimeError("The live API is missing the service-specific slot parameter")
    schemas = spec.get("components", {}).get("schemas", {})
    result_fields = schemas.get("ScheduleOut", {}).get("properties", {})
    if not {"services", "paused_client_ids", "resources", "bookings", "free_slots"} <= set(result_fields):
        raise RuntimeError("The live schedule response uses the old analytics schema")
    for field, required in (
        ("resources", {"working_dates"}),
        ("bookings", {"service_id", "resource_id"}),
        ("free_slots", {"service_id"}),
    ):
        field_def = result_fields[field]
        reference = field_def.get("items", {}).get("$ref")
        if not reference:
            raise RuntimeError(f"Cannot inspect {field} contract")
        schema_name = reference.rsplit("/", 1)[-1]
        properties = schemas.get(schema_name, {}).get("properties", {})
        if not required <= set(properties):
            raise RuntimeError(f"Live {field} contract has not been upgraded")


def verify_live_contract(client) -> None:
    # Only loopback, and only schema keys are processed: no users, secrets or client records.
    source = (
        "import json, urllib.request\\n"
        "with urllib.request.urlopen('http://127.0.0.1:8000/openapi.json', timeout=8) as r:\\n"
        "    spec = json.load(r)\\n"
        "paths = spec.get('paths', {})\\n"
        "assert '/internal/studio/businesses' in paths\\n"
        "params = {p.get('name') for p in paths['/internal/studio/schedule']['get']['parameters']}\\n"
        "assert 'slot_service_id' in params\\n"
        "schemas = spec['components']['schemas']\\n"
        "fields = schemas['ScheduleOut']['properties']\\n"
        "assert {'services', 'paused_client_ids', 'resources', 'bookings', 'free_slots'} <= set(fields)\\n"
        "expected = {'resources': {'working_dates'}, "
        "'bookings': {'service_id', 'resource_id'}, 'free_slots': {'service_id'}}\\n"
        "for field, names in expected.items():\\n"
        "    ref = fields[field]['items']['$ref'].rsplit('/', 1)[-1]\\n"
        "    assert names <= set(schemas[ref]['properties'])\\n"
        "print('insights_api_contract=ok')\\n"
    )
    run(client, "./venv/bin/python -c " + shlex.quote(source), 30)


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    apply = os.environ.get("APPLY", "false").strip().lower() == "true"
    password = os.environ.get("SERVGO_PASSWORD") or ""
    if not password:
        raise RuntimeError("Missing SERVER_SSH_PASSWORD; no server changes")
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Release must be dispatched from refs/heads/main")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    main_head = subprocess.check_output(["git", "rev-parse", "origin/main"], cwd=repo, text=True).strip()
    if head != main_head:
        raise RuntimeError("Refusing a stale or non-main release SHA")

    desired = {path: (repo / path).read_bytes() for path in FILES}
    known = {path: accepted_versions(repo, path) for path in FILES}
    for path, data in desired.items():
        compile(data, path, "exec")

    client = connect(password)  # SHA256-pinned host key
    sftp = client.open_sftp()
    originals: dict[str, bytes] = {}
    updated: list[str] = []
    backup = BACKUPS + "/" + str(time.time_ns())
    try:
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Web API is not active")
        if run(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health") != "200":
            raise RuntimeError("Web API health is not 200 before release")

        to_change = []
        conflicts = []
        for path in FILES:
            current = read_remote(sftp, ROOT + "/" + path)
            action = classify(current, desired[path], known[path])
            print(f"file={path} action={action} live_sha={digest(current)[:12]} target_sha={digest(desired[path])[:12]}")
            if action == "replace":
                originals[path] = current  # type: ignore[assignment]
                to_change.append(path)
            elif action == "conflict":
                conflicts.append(path)
        if conflicts:
            raise RuntimeError("Divergent or absent production files; refusing overwrite: " + ", ".join(conflicts))
        if not apply:
            print(f"insights_api_preflight=ok files_to_change={len(to_change)} production_untouched=true")
            return
        if not to_change:
            print("insights_api_release=already_current production_untouched=true")
            return

        run(client, "install -d -m 700 " + shlex.quote(backup))
        for path in to_change:
            source = ROOT + "/" + path
            destination = backup + "/" + path
            run(client, "install -d -m 700 " + shlex.quote(str(Path(destination).parent))
                + " && cp -p " + shlex.quote(source) + " " + shlex.quote(destination))
        for path in to_change:
            target = ROOT + "/" + path
            # Recheck just before writing to avoid overwriting another release.
            if read_remote(sftp, target) != originals[path]:
                raise RuntimeError("Production changed after preflight; refusing deployment")
            updated.append(path)
            mode = sftp.stat(target).st_mode & 0o777
            put(sftp, target, desired[path], mode)
            if read_remote(sftp, target) != desired[path]:
                raise RuntimeError("Failed to verify deployed file: " + path)

        run(client, "cd " + shlex.quote(ROOT) + " && ./venv/bin/python -m py_compile "
            + " ".join(shlex.quote(path) for path in FILES))
        # Verify that the new entry points are actually present before restart.
        check = ('from web.api.routes import studio_sso, studio_schedule, admin, crm; '
                 'assert hasattr(studio_sso, "studio_accessible_businesses"); '
                 'assert hasattr(studio_schedule, "studio_schedule"); '
                 'import inspect; '
                 'assert "slot_service_id" in inspect.signature(studio_schedule.studio_schedule).parameters')
        run(client, "cd " + shlex.quote(ROOT) + " && ./venv/bin/python -c "
            + shlex.quote(check), 60)
        run(client, "systemctl restart " + WEB_SERVICE, 65)
        wait_web_ready(client)
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Web API is not active after release")
        verify_live_contract(client)
        print(f"insights_api_release=success files_changed={len(to_change)} backup={backup}")
    except Exception:
        if updated:
            try:
                for path in reversed(updated):
                    target = ROOT + "/" + path
                    original = backup + "/" + path
                    run(client, "cp -p " + shlex.quote(original) + " " + shlex.quote(target))
                run(client, "systemctl restart " + WEB_SERVICE, 65)
                wait_web_ready(client)
                print("insights_api_rollback=success")
            except Exception as exc:
                print("insights_api_rollback=failed; manual recovery required")
                raise RuntimeError("Release and rollback both failed") from exc
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
