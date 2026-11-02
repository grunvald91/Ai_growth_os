"""Pure offline guards for analytics backend preflight; no SSH or production mutations."""
import hashlib
from pathlib import Path

from scripts.servgo_insights_api_release import FILES, accepted_versions, classify, digest, _check_live_contract


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


def _contract():
    return {
        "paths": {
            "/internal/studio/businesses": {"get": {}},
            "/internal/studio/schedule": {"get": {
                "parameters": [{"name": "slot_service_id"}],
            }},
        },
        "components": {"schemas": {
            "ScheduleOut": {"properties": {
                "services": {}, "paused_client_ids": {},
                "resources": {"items": {"$ref": "#/components/schemas/ResourceOut"}},
                "bookings": {"items": {"$ref": "#/components/schemas/BookingRow"}},
                "free_slots": {"items": {"$ref": "#/components/schemas/FreeSlots"}},
            }},
            "ResourceOut": {"properties": {"working_dates": {}}},
            "BookingRow": {"properties": {"service_id": {}, "resource_id": {}}},
            "FreeSlots": {"properties": {"service_id": {}}},
        }},
    }


def test_post_release_contract_accepts_all_required_api_fields():
    _check_live_contract(_contract())


def test_post_release_contract_refuses_partial_or_old_rollout():
    import copy
    import pytest

    valid = _contract()
    for category in ("business", "slot", "workdays", "crm", "bookings"):
        invalid = copy.deepcopy(valid)
        if category == "business":
            del invalid["paths"]["/internal/studio/businesses"]
        elif category == "slot":
            invalid["paths"]["/internal/studio/schedule"]["get"]["parameters"] = []
        elif category == "workdays":
            del invalid["components"]["schemas"]["ResourceOut"]["properties"]["working_dates"]
        elif category == "crm":
            del invalid["components"]["schemas"]["ScheduleOut"]["properties"]["paused_client_ids"]
        else:
            del invalid["components"]["schemas"]["BookingRow"]["properties"]["service_id"]
        with pytest.raises(RuntimeError):
            _check_live_contract(invalid)
