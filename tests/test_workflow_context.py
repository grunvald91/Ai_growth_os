from app.workflows.context import CONTEXT_LIMIT, _compact_context_to_bound, _serialized_size


def test_large_history_is_compacted_without_dropping_canonical_profile():
    context = {
        "product": {"id": "p", "name": "ServGo", "description": ""},
        "stable_context": "stable-" * 12000,
        "company_profile": {"profile": {"identity": {"name": "ServGo Growth"}}},
        "company_onboarding": {"state": "ready", "intake_in_progress": False},
        "records": {
            kind: [
                {"id": f"{kind}-{index}", "content": "x" * 2400, "title": "t" * 600}
                for index in range(20)
            ]
            for kind in ("artifact", "task", "experiment", "decision")
        },
        "limits": {
            "rows_per_kind": 20,
            "text_characters": 1500,
            "truncated_kinds": [],
        },
    }
    assert _serialized_size(context) > CONTEXT_LIMIT

    compacted = _compact_context_to_bound(context)

    assert _serialized_size(compacted) <= CONTEXT_LIMIT
    assert compacted["company_profile"]["profile"]["identity"]["name"] == "ServGo Growth"
    assert compacted["limits"]["history_compacted"] is True
    assert max(len(rows) for rows in compacted["records"].values()) <= 12
    assert compacted["stable_context"]
