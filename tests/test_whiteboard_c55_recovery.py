"""The one-time video rescue writes only a validated, reusable local decision."""
import pytest

from app.content.whiteboard.checkpoints import CheckpointStore
from app.content.whiteboard.review import (
    VisualDecision, IssueDecision, VISUAL_DECISION_FALLBACK_REASON,
)
from scripts.operations.restore_whiteboard_c55_decision import save_decision
from test_whiteboard import request
from test_whiteboard_checkpoints import MODELS


@pytest.mark.asyncio
async def test_no_provider_calls_needed_to_persist_local_c55_fallback(tmp_path):
    store = CheckpointStore(tmp_path / "stages", request(), models=MODELS, visual_review=True)
    decision = VisualDecision(decisions=[
        IssueDecision(issue_id="s0-major", accept=False, reason=VISUAL_DECISION_FALLBACK_REASON),
        IssueDecision(issue_id="s1-minor", accept=False, reason=VISUAL_DECISION_FALLBACK_REASON),
    ])
    await save_decision(store, decision)
    assert VisualDecision.model_validate_json(
        store.load("visual-decision", limit=64_000)
    ) == decision
    assert store.reset_unfinished() == []
    await save_decision(store, decision)
    assert store.load("visual-decision", limit=64_000) == decision.model_dump_json().encode()
