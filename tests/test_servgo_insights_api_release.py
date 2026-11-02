"""Pure offline guards for analytics backend preflight; no SSH or production mutations."""
import hashlib
from pathlib import Path

from scripts.servgo_insights_api_release import FILES, accepted_versions, classify, digest


def test_known_hash_allows_a_reviewed_update_but_never_unknown_drift():
    expected = b"new"
    before = b"version committed earlier"
    known = {digest(before)}
    assert classify(expected, expected, known) == "already"
    assert classify(before, expected, known) == "replace"
    assert classify(b"manual server hotfix", expected, known) == "conflict"
    assert classify(None, expected, known) == "conflict"


def test_release_is_scoped_to_four_existing_api_routes():
    assert set(FILES) == {
        "web/api/routes/admin.py", "web/api/routes/crm.py",
        "web/api/routes/studio_schedule.py", "web/api/routes/studio_sso.py",
    }
    assert not any("database" in name or "migration" in name for name in FILES)


def test_git_history_is_used_as_a_guard_not_a_blanket_overwrite():
    root = Path(__file__).resolve().parents[1]
    for path in FILES:
        historical = accepted_versions(root, path)
        current = (root / path).read_bytes()
        assert digest(current) in historical
