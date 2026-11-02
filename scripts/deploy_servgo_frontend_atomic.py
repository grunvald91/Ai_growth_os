"""Verified, versioned ServGo frontend release.

With APPLY=false this performs read-only preflight. With APPLY=true, it stages
a full Vite dist in a unique release folder, checks every SHA256, switches the
dist symlink once, checks HTTP and the served SW checksum, and rolls back on
failure. First-time migration from a real dist directory preserves the old
directory in releases/legacy-*; subsequent switches are atomic.
"""
from __future__ import annotations

import ast
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


STUDIO_SSO = "web/api/routes/studio_sso.py"


def backend_equivalent(path: str, current: bytes | None, candidate: bytes) -> bool:
    """Permit a text-only production SSO difference without overwriting it.

    Other backend files must remain byte-for-byte identical. We accept only
    the exact pre-existing production SSO shape: two identical declarations
    appended at the end, with every other AST node in identical order.
    Runtime-ignored comments may differ. The live SSO file is not changed.
    """
    if current == candidate:
        return True
    if path != STUDIO_SSO or current is None:
        return False
    try:
        current_tree = ast.parse(current.decode("utf-8"), filename=path)
        candidate_tree = ast.parse(candidate.decode("utf-8"), filename=path)
        canonical = lambda node: ast.dump(node, include_attributes=False)
        if canonical(current_tree) == canonical(candidate_tree):
            return True
        # The existing production SSO patch appended exactly these two
        # unmodified definitions after all other declarations. Preserve it
        # only when *everything else*, including decorator AST, matches main
        # in the same order. Never allow arbitrary definition reordering.
        moved = ("_businesses_for_studio", "studio_accessible_businesses")
        def function_name(node):
            return node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        current_nodes = current_tree.body
        candidate_nodes = candidate_tree.body
        if len(current_nodes) < 2 or tuple(function_name(n) for n in current_nodes[-2:]) != moved:
            return False
        if [function_name(n) for n in candidate_nodes].count(moved[0]) != 1:
            return False
        if [function_name(n) for n in candidate_nodes].count(moved[1]) != 1:
            return False
        original_moved = [next(n for n in candidate_nodes if function_name(n) == name) for name in moved]
        if [canonical(n) for n in current_nodes[-2:]] != [canonical(n) for n in original_moved]:
            return False
        other_candidate = [n for n in candidate_nodes if function_name(n) not in moved]
        if [canonical(n) for n in current_nodes[:-2]] != [canonical(n) for n in other_candidate]:
            return False
        return True
    except (SyntaxError, UnicodeError, ValueError):
        return False


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
            if not backend_equivalent(path, server, candidate):
                mismatches.append(path)
            elif server != candidate:
                print("backend_sso_ast_equivalent=true; preserving_server_studio_sso=yes", flush=True)
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

        # Verify critical same-origin navigation and owner-only gates *before*
        # finalizing release; any failure uses the existing symlink rollback.
        routes = {
            ("servgo.ru", "/mail"): {"200"},
            ("servgo.ru", "/workspace/mail"): {"200"},
            ("servgo.ru", "/workspace/growth"): {"200"},
            ("servgo.ru", "/api/owner-mail/workspace"): {"303"},
            ("servgo.ru", "/api/owner-mail/api/company-search"): {"401", "403"},
            ("mail.servgo.ru", "/"): {"200"},
        }
        for (domain, path), expected_codes in routes.items():
            status = run(
                client, "curl -sS -o /dev/null -w '%{http_code}' --max-time 12"
                + " --resolve " + sh(domain + ":443:127.0.0.1")
                + " " + sh("https://" + domain + path)
            )
            if status not in expected_codes:
                raise RuntimeError("Post-release route verification failed: " + domain + path + " HTTP " + status)
            print("frontend_route_check=" + domain + path + " http=" + status, flush=True)
        # The internal Studio API is NOT nginx-proxied at the public /internal/*
        # path: that public URL resolves to the SPA (HTTP 200). Verify the
        # actual FastAPI handler on the loopback service instead.
        studio_code = run(
            client, "curl -sS -o /dev/null -w '%{http_code}' --max-time 8 "
            + sh("http://127.0.0.1:8000/internal/studio/businesses?user_id=0")
        )
        if studio_code != "403":
            raise RuntimeError("Internal Studio SSO accepted unauthenticated request: HTTP " + studio_code)
        print("frontend_studio_internal_auth_check=http_403", flush=True)
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
