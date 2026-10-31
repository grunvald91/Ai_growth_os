"""Guarded, scoped ServGo PWA API rollout; defaults to read-only preflight.

The existing BookingBot frontend deployment does NOT ship web/api/. This tool
deploys only the reviewed backend dependencies of the integrated Growth/Mail/SEO
candidate, refusing any production-only divergence instead of overwriting it.
Runs only on an explicit manual operator invocation with APPLY=true.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.deploy_business_creation_and_reminders import ROOT, WEB_SERVICE, connect, put, run, wait_web_ready

BASE_SHA = "fe6777bf5238559ead8e660157d570dca277fed8"
FILES = (
    "database/models.py",  # SeoBookingAttribution ORM mapping; original DB schema is preserved.
    "web/api/main.py",
    "web/api/routes/client.py",  # Public business blog feed and article routes
    "web/api/routes/seo_pages.py",  # Server-side HTML and sitemap for company articles.
    "web/api/routes/seo_reporting.py",  # Owner-only booking attribution aggregation.
    "web/api/seo_referrals.py",  # Signed attribution and verified booking links.
    "web/api/routes/studio_sso.py",
    "web/api/routes/owner_mail.py",
    "web/api/routes/mail_pwa.py",
    "web/api/routes/mail_campaign_dashboard.html",
    "web/api/routes/mail_campaign_ui.py",
    "web/api/routes/owner_mail_ui.html",
    "web/api/routes/servgo-air-theme.js",
    "web/api/routes/servgo-air-tokens.css",
    "web/api/routes/servgo-mail-air.css",
    "web/api/routes/servgo-mail-theme-picker.js",
)
# These are new backend files; they may legitimately be missing on the server.
# Every other file must exist at BASE_SHA and may only be updated from that baseline.
ADDED_FILES = frozenset({
    "web/api/routes/seo_pages.py",
    "web/api/routes/seo_reporting.py",
    "web/api/seo_referrals.py",
    "web/api/routes/mail_pwa.py",
    "web/api/routes/servgo-air-theme.js",
    "web/api/routes/servgo-air-tokens.css",
    "web/api/routes/servgo-mail-air.css",
    "web/api/routes/servgo-mail-theme-picker.js",
})
BACKUP_ROOT = ROOT + "/.servgo-pwa-api-releases"


def sha(value: bytes | None) -> str:
    return hashlib.sha256(value).hexdigest() if value is not None else "absent"


def classify(current: bytes | None, baseline: bytes | None, desired: bytes) -> str:
    if current == desired:
        return "already"
    if current == baseline:
        return "create" if baseline is None else "update"
    return "conflict"


def baseline(repo: Path, path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "show", f"{BASE_SHA}:{path}"],
        cwd=repo, capture_output=True, check=False,
    )
    if proc.returncode == 0:
        return proc.stdout
    # These files were created by this feature; other missing baseline paths
    # would be an unexpected error rather than permission to overwrite.
    if path in ADDED_FILES:
        return None
    raise RuntimeError(f"Baseline missing for existing file: {path}")


def read(sftp, remote: str) -> bytes | None:
    try:
        with sftp.open(remote, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def main() -> None:
    repo = Path(__file__).resolve().parent.parent
    apply = os.environ.get("APPLY", "false").strip().lower() == "true"
    password = os.environ.get("SERVGO_PASSWORD", "")
    if not password:
        raise RuntimeError("SERVER_SSH_PASSWORD is required")
    # Do not let a source ref from elsewhere silently replace production.
    if not (repo / "web/frontend/public/sw.js").read_text().count("servgo-pwa-v4"):
        raise RuntimeError("Expected integrated v4 source branch")
    # The SQL migration stays a distinct, explicitly approved database step:
    # code release alone must not silently change live schema or enable tracking.
    desired = {path: (repo / path).read_bytes() for path in FILES}
    original_ref = {path: baseline(repo, path) for path in FILES}
    for path in FILES:
        if path.endswith(".py"):
            compile(desired[path], path, "exec")

    client = connect(password)  # fingerprint pinned by the existing shared helper
    sftp = client.open_sftp()
    original: dict[str, bytes | None] = {}
    permission: dict[str, int] = {}
    updated: list[str] = []
    stamp = str(int(time.time()))
    backup_dir = BACKUP_ROOT + "/" + stamp
    try:
        if run(client, "systemctl is-active " + WEB_SERVICE) != "active":
            raise RuntimeError("Booking API is not active")
        if run(client, "curl -fsS --max-time 5 http://127.0.0.1:8000/health") != '{"status":"ok"}':
            # The exact JSON whitespace isn't contractual; verify HTTP status below.
            code = run(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/health")
            if code != "200":
                raise RuntimeError("Booking API health not 200")

        todo: list[str] = []
        conflicts: list[str] = []
        for path in FILES:
            target = ROOT + "/" + path
            before = read(sftp, target)
            original[path] = before
            if before is not None:
                permission[path] = sftp.stat(target).st_mode & 0o777
            state = classify(before, original_ref[path], desired[path])
            print(f"backend_file={path} action={state} current_sha256={sha(before)[:16]} intended_sha256={sha(desired[path])[:16]}")
            if state == "conflict":
                conflicts.append(path)
            elif state != "already":
                todo.append(path)

        if conflicts:
            raise RuntimeError("Refusing to overwrite divergent production files: " + ", ".join(conflicts))
        if not apply:
            print(f"preflight_ok=true changes={len(todo)} production_untouched=true")
            return
        if not todo:
            print("already_deployed=true production_untouched=true")
            return

        # Every source check passed before the first write. Back up on server.
        run(client, "install -d -m 0700 " + shlex.quote(BACKUP_ROOT) +
            " && install -d -m 0700 " + shlex.quote(backup_dir))
        for path in todo:
            target = ROOT + "/" + path
            if original[path] is not None:
                backup = backup_dir + "/" + path
                run(client, "install -d -m 0700 " + shlex.quote(str(Path(backup).parent)) +
                    " && cp -p " + shlex.quote(target) + " " + shlex.quote(backup))
            run(client, "install -d -m 0755 " + shlex.quote(str(Path(target).parent)))
            put(sftp, target, desired[path], permission.get(path, 0o644))
            updated.append(path)
            if sha(read(sftp, target)) != sha(desired[path]):
                raise RuntimeError("SHA mismatch after write: " + path)

        run(client, "cd " + shlex.quote(ROOT) +
            " && ./venv/bin/python -m py_compile " +
            " ".join(shlex.quote(path) for path in todo if path.endswith(".py")), 50)
        run(client, "systemctl restart " + WEB_SERVICE, 65)
        wait_web_ready(client)
        code = run(client, "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/owner-mail/workspace")
        # Mail gateway remains OFF; this private endpoint must NOT become public.
        if code not in ("401", "403", "404"):
            raise RuntimeError("Unexpected unauthenticated Mail workspace response: " + code)
        print(f"api_release_ok=true changed={len(todo)} backup_dir={backup_dir} flags_unchanged=true")
    except Exception:
        if updated:
            # Restore only exact files we changed, and restart the old API.
            try:
                for path in reversed(updated):
                    target = ROOT + "/" + path
                    if original[path] is None:
                        run(client, "rm -f " + shlex.quote(target))
                    else:
                        backup = backup_dir + "/" + path
                        run(client, "cp -p " + shlex.quote(backup) + " " + shlex.quote(target))
                run(client, "systemctl restart " + WEB_SERVICE, 65)
                wait_web_ready(client)
                print("api_rollback=ok")
            except Exception as rollback_error:
                print("api_rollback=FAILED: manual recovery required")
                raise RuntimeError("API rollout failed and rollback failed") from rollback_error
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
