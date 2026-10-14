"""Persist supplementary critic evidence without changing existing workflow output.

No tools, publishing or LLM calls. A dedicated artifact keeps existing
typed payloads backwards compatible and survives a rejected author edit.
"""
from __future__ import annotations

import json

from app.models.domain import AgentRun
from app.services.artifacts import ArtifactService


def critic_cost_details(response, **extra) -> dict:
    audit = (response.quality_review or {}) if response is not None else {}
    return {
        "cost_known": False,
        **extra,
        "review_status": audit.get("status"),
        "extra_usage_by_model": audit.get("extra_usage_by_model", []),
        "usage_incomplete": audit.get("status") in {"review_unavailable", "revision_unavailable"},
    }


def persist_critic_audit(db, *, product_id, task_id, run_id, response) -> str | None:
    if response is None or not response.quality_review:
        return None
    record = ArtifactService(db).create(
        product_id=product_id, task_id=task_id, agent_run_id=run_id,
        kind="agent_quality_review",
        title="AI reviewer decisions and usage",
        content=json.dumps(response.quality_review, ensure_ascii=False),
        status="draft",
    )
    run = db.get(AgentRun, run_id)
    references = list(run.output_references or [])
    references.append({"kind": "artifact", "id": str(record.id)})
    run.output_references = references
    return str(record.id)
