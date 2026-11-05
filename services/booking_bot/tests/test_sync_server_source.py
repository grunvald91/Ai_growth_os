from scripts.sync_server_source import allowed, classify, looks_secret

import hashlib


def h(data):
    return hashlib.sha256(data).hexdigest()


def test_same_behind_unique_new():
    assert classify("a.py", b"x", b"x", set()) == "same"
    assert classify("a.py", b"old", b"new", {h(b"old")}) == "behind"
    assert classify("a.py", b"hotfix", b"new", {h(b"old")}) == "unique"
    assert classify("a.py", b"fresh", None, set()) == "new"


def test_a_file_with_a_secret_is_never_taken():
    assert classify("a.py", b"TOKEN = 'abcdefgh12345678'", b"x", set()) == "secret"
    assert looks_secret(b"postgresql+asyncpg://bot:hunter2pass@localhost/db")
    assert looks_secret(b"-----BEGIN RSA PRIVATE KEY-----")
    assert not looks_secret(b"os.getenv('STUDIO_SSO_SECRET', '')")
    assert not looks_secret(b"password: Optional[str] = None")


def test_paths_with_credentials_or_build_output_are_skipped():
    assert not allowed("web/frontend/dist/app.js")
    assert not allowed("config/credentials.py")
    assert allowed("web/api/main.py")
