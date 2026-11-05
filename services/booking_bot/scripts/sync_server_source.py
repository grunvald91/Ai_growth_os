"""Bring the production source into GitHub (server -> branch), without ever printing file contents.

For every tracked file that exists on the server and differs from `main`:
  * if the server copy equals some earlier revision of that file, the server is just behind (e.g. an older
    copy of a front-end source) and nothing is taken from it;
  * otherwise the server copy is unique (a hotfix made on the server, or `main` plus this release's lines) and is
    committed to a new branch for review. A copy that looks like it holds a secret is NOT committed (path only).
Source files that exist on the server but are not tracked at all are committed the same way.
Files that exist only in GitHub (front-end sources, tests, docs) stay: the server never had them.
The branch is only pushed; nothing is merged and nothing on the server is changed.
"""
import base64
import hashlib
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path

import paramiko

HOST = "155.212.162.47"
FP = "uQJp3lT4golKb3lsDTCFozGWR1ihlu1w/Oa4cSzCdpU"
ROOT = "/opt/booking_bot/"
MAX_SIZE = 2_000_000
SOURCE_ROOTS = ("bot", "database", "web/api", "webapp", "scripts", "migrations")
SOURCE_EXTENSIONS = {".py", ".js", ".ts", ".tsx", ".html", ".css", ".yaml", ".yml", ".sql"}
SECRET_PATTERNS = [
    re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),                      # Telegram bot token
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    re.compile(rb"(?i)(?:postgres(?:ql)?|mysql|redis|amqp)(?:\+\w+)?://[^\s:/@'\"]+:[^\s@'\"]+@"),
]


def allowed(path: str) -> bool:
    segments = path.lower().split("/")
    lowered = path.lower()
    return (path != ".env.example"
            and not any(x in segments for x in (".git", ".github", "venv", "node_modules", "dist", "__pycache__", ".env"))
            and not any(x in lowered for x in ("password", "credential", "private_key", ".pem", ".sqlite", ".db", ".log")))


def looks_secret(data: bytes) -> bool:
    return any(pattern.search(data) for pattern in SECRET_PATTERNS)


def history_hashes(path: str) -> set[str]:
    """sha256 of every revision this file ever had in git."""
    revisions = subprocess.run(["git", "log", "--all", "--format=%H", "--", path], capture_output=True, text=True,
                               check=True).stdout.split()
    hashes = set()
    for revision in revisions:
        shown = subprocess.run(["git", "show", f"{revision}:{path}"], capture_output=True)
        if shown.returncode == 0:
            hashes.add(hashlib.sha256(shown.stdout).hexdigest())
    return hashes


def classify(path: str, server: bytes, repo: bytes | None, past: set[str]) -> str:
    """same | behind | unique | secret | new."""
    digest = hashlib.sha256(server).hexdigest()
    if repo is not None and hashlib.sha256(repo).hexdigest() == digest:
        return "same"
    if looks_secret(server):
        return "secret"
    if repo is None:
        return "new"
    return "behind" if digest in past else "unique"


class Pinned(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client, hostname, key):
        actual = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if actual != FP:
            raise paramiko.SSHException("Host key mismatch")
        client.get_host_keys().add(hostname, key.get_name(), key)


def connect():
    for attempt in range(3):
        conn = paramiko.SSHClient()
        conn.set_missing_host_key_policy(Pinned())
        try:
            conn.connect(HOST, port=22, username="root", password=os.environ["SERVER_PASSWORD"], look_for_keys=False,
                         allow_agent=False, timeout=12, banner_timeout=25, auth_timeout=15)
            return conn
        except (paramiko.SSHException, OSError, EOFError):
            conn.close()
            if attempt == 2:
                raise
            time.sleep(3)


def git(*args):
    subprocess.run(["git", *args], check=True)


def main():
    branch = sys.argv[1]
    tracked = [p for p in subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0") if p and allowed(p)]
    tracked_set = set(tracked)
    ssh = connect()
    sftp = ssh.open_sftp()
    take, counts, skipped = {}, {"same": 0, "behind": 0, "unique": 0, "secret": 0, "new": 0}, []
    try:
        for path in tracked:
            try:
                info = sftp.stat(ROOT + path)
                if not stat.S_ISREG(info.st_mode):
                    continue
                if info.st_size > MAX_SIZE:
                    skipped.append(path + " (too large)")
                    continue
                with sftp.open(ROOT + path, "rb") as handle:
                    data = handle.read()
            except FileNotFoundError:
                continue
            repo = Path(path).read_bytes()
            kind = classify(path, data, repo, history_hashes(path) if data != repo else set())
            counts[kind] += 1
            if kind == "unique":
                take[path] = data
            elif kind == "secret":
                skipped.append(path + " (looks like it holds a secret; not copied)")
            elif kind == "behind":
                print("server_is_behind_git: " + path)

        def scan(directory):
            try:
                entries = sftp.listdir_attr(ROOT + directory)
            except FileNotFoundError:
                return
            for entry in entries:
                path = directory + "/" + entry.filename
                if not allowed(path) or entry.filename.startswith(".") or entry.filename.endswith((".bak", ".old", ".orig")):
                    continue
                if stat.S_ISDIR(entry.st_mode):
                    scan(path)
                elif (stat.S_ISREG(entry.st_mode) and path not in tracked_set and Path(path).suffix in SOURCE_EXTENSIONS
                      and entry.st_size <= MAX_SIZE):
                    with sftp.open(ROOT + path, "rb") as handle:
                        data = handle.read()
                    if looks_secret(data):
                        counts["secret"] += 1
                        skipped.append(path + " (looks like it holds a secret; not copied)")
                    else:
                        counts["new"] += 1
                        take[path] = data

        for directory in SOURCE_ROOTS:
            scan(directory)
    finally:
        sftp.close()
        ssh.close()
    print("counts=" + " ".join(f"{k}={v}" for k, v in counts.items()))
    print("will_commit=" + (", ".join(sorted(take)) or "nothing"))
    for item in skipped:
        print("skipped=" + item)
    if not take:
        print("nothing_to_commit=yes")
        return
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    git("checkout", "-B", branch, "origin/main")
    for path, data in take.items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git("add", "--", *take)
    git("commit", "-m", "Синхронизация с сервером: файлы, которые на боевом сервере отличаются от main\n\n"
        "Взято с /opt/booking_bot без изменений (кроме файлов с подозрением на секрет). Для проверки перед слиянием.")
    git("push", "--force", "origin", branch)
    print("pushed_branch=" + branch)


if __name__ == "__main__":
    main()
