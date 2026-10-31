"""Verified, versioned ServGo frontend release.

With APPLY=false this performs read-only preflight. With APPLY=true, it stages
a full Vite dist in a unique release folder, checks every SHA256, switches the
dist symlink once, checks HTTP and the served SW checksum, and rolls back on
failure. First-time migration from a real dist directory preserves the old
directory in releases/legacy-*; subsequent switches are atomic.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.deploy_business_creation_and_reminders import ROOT, connect, run  # noqa: E402
from scripts.servgo_unified_api_release import FILES  # noqa: E402

FRONT = ROOT + "/web/frontend"
DIST = FRONT + "/dist"
RELEASES = FRONT + "/releases"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_name(commit: str, stamp: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full tested Git commit SHA")
    if not re.fullmatch(r"[0-9]+", stamp):
        raise ValueError("Bad release stamp")
    return f"servgo-{commit[:16]}-{stamp}"


def sh(value: str) -> str:
    return shlex.quote(value)


def read_file(sftp, path: str) -> bytes | None:
    try:
        with sftp.open(path, "rb") as handle:
            return handle.read()
    except OSError:
        return None


def main():
    root = Path(__file__).resolve().parent.parent
    local = root / "web/frontend/dist"
    if not (local / "index.html").is_file() or not (local / "sw.js").is_file():
        raise RuntimeError("Frontend dist not built")
    if b"servgo-pwa-v4" not in (local / "sw.js").read_bytes():
        raise RuntimeError("Expected SW v4 for offline release")
    assets_manifest = local / "offline-assets.json"
    if not assets_manifest.is_file():
        raise RuntimeError("Missing offline-assets.json")
    revision = hashlib.sha256(assets_manifest.read_bytes()).hexdigest()[:16]
    revision_marker = f"// servgo-offline-manifest-sha256: {revision}".encode()
    if revision_marker not in (local / "sw.js").read_bytes():
        raise RuntimeError("Built service worker does not match offline asset manifest")

    import json
    paths = json.loads(assets_manifest.read_text(encoding="utf-8"))
    if not isinstance(paths, list) or not paths or any(
        not isinstance(x, str) or not x.startswith("/assets/")
        or not (local / x.lstrip("/")).is_file() for x in paths
    ):
        raise RuntimeError("Offline manifest references missing or non-asset files")

    files = sorted(x for x in local.rglob("*") if x.is_file())
    expected = {x.relative_to(local).as_posix(): sha(x.read_bytes()) for x in files}
    if len(expected) < 8:
        raise RuntimeError("Frontend output unexpectedly incomplete")
    commit = os.environ.get("GITHUB_SHA", "")
    stamp = str(int(time.time()))
    name = release_name(commit, stamp)
    apply = os.environ.get("APPLY", "false").lower() == "true"
    password = os.environ.get("SERVGO_PASSWORD", "")
    if not password:
        raise RuntimeError("SERVER_SSH_PASSWORD missing")
    client = connect(password)
    sftp = client.open_sftp()
    prev_link = None
    legacy = None
    switched = False
    new_release = RELEASES + "/" + name
    try:
        if run(client, "systemctl is-active nginx") != "active":
            raise RuntimeError("nginx inactive")
        if run(client, "nginx -t >/dev/null 2>&1 && echo ok") != "ok":
            raise RuntimeError("nginx configuration invalid")
        # The backend must be deployed independently FIRST. Never publish a
        # frontend linked to API handlers that are not present on the server.
        mismatches = []
        for path in FILES:
            server = read_file(sftp, ROOT + "/" + path)
            candidate = (root / path).read_bytes()
            if server != candidate:
                mismatches.append(path)
        print(f"frontend_preflight=ok files={len(files)} backend_ready={str(not mismatches).lower()}")
        for item in mismatches:
            print("backend_release_required=" + item)
        try:
            current_mode = sftp.lstat(DIST).st_mode
            current_is_link = stat.S_ISLNK(current_mode)
            current_is_dir = stat.S_ISDIR(current_mode)
        except OSError:
            raise RuntimeError("Current production dist path missing")
        if not (current_is_link or current_is_dir):
            raise RuntimeError("Production dist is neither directory nor symlink")
        print("frontend_layout=" + ("symlink" if current_is_link else "legacy-directory"))
        if not apply:
            print("frontend_production_untouched=true")
            return
        if mismatches:
            raise RuntimeError("Backend release must be applied first; refusing frontend switch")

        run(client, "install -d -m 0755 " + sh(RELEASES) +
            " && install -d -m 0755 " + sh(new_release))
        for file in files:
            rel = file.relative_to(local).as_posix()
            dest = new_release + "/" + rel
            run(client, "install -d -m 0755 " + sh(str(Path(dest).parent)))
            # Write to a temporary file, verify, then use an atomic per-file rename.
            temp = dest + ".part"
            with sftp.open(temp, "wb") as writer:
                writer.write(file.read_bytes())
            sftp.chmod(temp, 0o644)
            sftp.posix_rename(temp, dest)
            remote = read_file(sftp, dest)
            if remote is None or sha(remote) != expected[rel]:
                raise RuntimeError("Staged file SHA mismatch: " + rel)

        # Keep previous content-hashed JS/CSS chunks accessible for existing
        # browser sessions and installed PWA clients during the rollout.
        # Never overwrite the freshly built artifacts.
        run(client, "if [ -d " + sh(DIST + "/assets") + " ]; then " +
            "install -d -m 0755 " + sh(new_release + "/assets") +
            " && cp -a -n " + sh(DIST + "/assets/.") + " " +
            sh(new_release + "/assets/") + "; fi")

        if current_is_link:
            prev_link = run(client, "readlink " + sh(DIST))
            if not prev_link.startswith("releases/"):
                raise RuntimeError("Unrecognized dist symlink target; refusing")
        else:
            legacy = RELEASES + "/legacy-" + stamp
            # The initial migration has one brief gap between renames. Every
            # subsequent release is an atomic symlink swap.
            run(client, "mv " + sh(DIST) + " " + sh(legacy))
        link_target = "releases/" + name
        temp_link = FRONT + "/.dist-next-" + stamp
        run(client, "ln -s " + sh(link_target) + " " + sh(temp_link) +
            " && mv -Tf " + sh(temp_link) + " " + sh(DIST))
        switched = True

        code = run(client, "curl -sk -o /dev/null -w '%{http_code}' " +
                   "--resolve servgo.ru:443:127.0.0.1 --max-time 12 https://servgo.ru/")
        if code != "200":
            raise RuntimeError("Homepage HTTP after release: " + code)
        sw_hash = run(client, "curl -skf --resolve servgo.ru:443:127.0.0.1 " +
                      "--max-time 12 https://servgo.ru/sw.js | sha256sum | cut -d' ' -f1")
        if sw_hash != expected["sw.js"]:
            raise RuntimeError("Live HTTPS SW checksum is not the staged version")
        print(f"frontend_release_ok=true commit={commit} assets_verified={len(files)} release={name}")
    except Exception:
        if switched or legacy:
            try:
                if prev_link:
                    path = FRONT + "/.dist-rollback-" + stamp
                    run(client, "ln -s " + sh(prev_link) + " " + sh(path) +
                        " && mv -Tf " + sh(path) + " " + sh(DIST))
                elif legacy:
                    run(client, "[ ! -L " + sh(DIST) + " ] || rm " + sh(DIST) +
                        "; mv " + sh(legacy) + " " + sh(DIST))
                print("frontend_rollback=ok")
            except Exception as ex:
                print("frontend_rollback=failed; manual restore required")
                raise RuntimeError("Frontend switch and rollback both failed") from ex
        raise
    finally:
        sftp.close()
        client.close()


if __name__ == "__main__":
    main()
