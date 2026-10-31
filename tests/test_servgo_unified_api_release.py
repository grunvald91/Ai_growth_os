"""Pure release-contract checks, no live production access."""
from pathlib import Path

from scripts.servgo_unified_api_release import BASE_SHA, FILES, classify, sha, baseline

ROOT = Path(__file__).resolve().parents[1]


def test_source_is_pinned_to_verified_booking_main_and_only_api_files():
    assert BASE_SHA == 'fe6777bf5238559ead8e660157d570dca277fed8'
    assert len(FILES) == 12
    assert 'web/api/routes/client.py' in FILES, 'SEO company blogs must ship with the backend'
    assert all(p.startswith('web/api/') for p in FILES)
    assert len(set(FILES)) == len(FILES)


def test_never_overwrite_a_server_only_hotfix():
    assert classify(b'production-hotfix', b'old-version', b'new-version') == 'conflict'
    assert classify(b'old-version', b'old-version', b'new-version') == 'update'
    assert classify(b'new-version', b'old-version', b'new-version') == 'already'
    assert classify(None, None, b'new-route') == 'create'
    assert classify(b'unreviewed-route', None, b'new-route') == 'conflict'


def test_reviewed_baselines_and_candidate_files_exist():
    for path in FILES:
        expected = (ROOT / path).read_bytes()
        old = baseline(ROOT, path)
        assert expected
        if old is not None:
            assert old != expected, path
        assert sha(expected) != 'absent'
