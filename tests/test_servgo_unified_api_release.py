"""Pure release-contract checks, no live production access."""
from pathlib import Path

import pytest

from scripts import servgo_unified_api_release as release
from scripts.servgo_unified_api_release import BASE_SHA, FILES, ADDED_FILES, classify, sha, baseline

ROOT = Path(__file__).resolve().parents[1]


def test_source_is_pinned_to_verified_booking_main_and_only_api_files():
    assert BASE_SHA == 'fe6777bf5238559ead8e660157d570dca277fed8'
    assert len(FILES) == 16
    required = {
        'web/api/main.py',
        'web/api/routes/client.py',
        'web/api/routes/seo_pages.py',
        'web/api/routes/seo_reporting.py',
        'web/api/seo_referrals.py',
        'database/models.py',
    }
    assert required <= set(FILES), 'SEO blogs, booking attribution and their ORM model must ship together'
    assert all(p.startswith('web/api/') or p == 'database/models.py' for p in FILES)
    assert ADDED_FILES <= set(FILES)
    assert not {'migrations/add_seo_booking_attributions.sql', 'web/frontend/public/sw.js'} & set(FILES)
    assert len(set(FILES)) == len(FILES)


def test_never_overwrite_a_server_only_hotfix():
    assert classify(b'production-hotfix', b'old-version', b'new-version') == 'conflict'
    assert classify(b'old-version', b'old-version', b'new-version') == 'update'
    assert classify(b'new-version', b'old-version', b'new-version') == 'already'
    assert classify(None, None, b'new-route') == 'create'
    assert classify(b'unreviewed-route', None, b'new-route') == 'conflict'


def test_article_routes_and_booking_attribution_have_pinned_deploy_dependencies():
    # Root ORM model is not under web/api/. Without it, the new
    # seo_reporting import would crash even if seo_pages.py deployed.
    code = (ROOT / 'web/api/main.py').read_text(encoding='utf-8')
    assert 'seo_pages' in code and 'seo_reporting' in code
    reporting = (ROOT / 'web/api/routes/seo_reporting.py').read_text(encoding='utf-8')
    assert 'SeoBookingAttribution' in reporting
    models = (ROOT / 'database/models.py').read_text(encoding='utf-8')
    assert 'class SeoBookingAttribution' in models
    migration = (ROOT / 'migrations/add_seo_booking_attributions.sql').read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS seo_booking_attributions' in migration


def test_reviewed_baselines_and_candidate_files_exist():
    for path in FILES:
        expected = (ROOT / path).read_bytes()
        old = baseline(ROOT, path)
        assert expected
        if old is not None:
            assert old != expected, path
        assert sha(expected) != 'absent'


def test_missing_remote_file_is_distinct_from_permission_or_transport_errors():
    class FakeSFTP:
        def __init__(self, error):
            self.error = error

        def open(self, path, mode):
            raise self.error

    assert release.read(FakeSFTP(FileNotFoundError("not found")), "/missing") is None
    with pytest.raises(PermissionError):
        release.read(FakeSFTP(PermissionError("forbidden")), "/private")
    with pytest.raises(OSError, match="connection closed"):
        release.read(FakeSFTP(OSError("connection closed")), "/unreachable")


def test_failed_upload_is_registered_for_rollback_before_write(monkeypatch):
    registered = []

    def failing_put(sftp, target, contents, mode):
        assert registered == ["web/api/main.py"]
        raise OSError("rename failed after upload")

    monkeypatch.setattr(release, "put", failing_put)
    with pytest.raises(OSError, match="rename failed"):
        release.put_tracked(None, "/opt/booking_bot/web/api/main.py", b"new",
                            0o644, "web/api/main.py", registered)
    assert registered == ["web/api/main.py"]
