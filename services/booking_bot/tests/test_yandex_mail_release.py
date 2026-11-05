"""Fail-closed deployment identity and exact-file scope checks."""
from pathlib import Path
from scripts import release_yandex_mail as r


def test_server_version_match_or_refuse():
    previous = b"approved previous content"
    current = b"reviewed new content"
    assert r.decision(previous, r.blob_sha(previous), current) == "replace"
    assert r.decision(current, r.blob_sha(previous), current) == "already_current"
    assert r.decision(b"unknown server edit", r.blob_sha(previous), current) == "drift"


def test_release_changes_only_two_mail_files_and_compiles():
    assert set(r.TARGETS) == {
        "web/api/routes/mail_company_search.py",
        "web/api/routes/mail_campaign_dashboard.html",
    }
    for target in r.TARGETS:
        assert Path(target).is_file()
    compile(Path("web/api/routes/mail_company_search.py").read_text(),
            "mail_company_search.py", "exec")
