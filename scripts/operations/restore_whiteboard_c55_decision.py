"""One-time, no-provider repair of a single failed Whiteboard decision checkpoint.

Intentionally never resumes a job, raises a call limit, or calls any provider.
Run in the production API container as 'python -' (dry-run), then 'python - --apply'.
The later paid continuation remains solely in the owner's existing approval UI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from app.content.whiteboard.checkpoints import CheckpointStore, CheckpointError
from app.content.whiteboard.contracts import WhiteboardStoryboard
from app.content.whiteboard.jobs import AGENT, call_limit, job_directory, owner_task, records
from app.content.whiteboard.review import (
    VisualReview, VisualDecision, IssueDecision, VISUAL_DECISION_FALLBACK_REASON,
)
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Task, TaskStatus

TASK_ID = UUID("c55fd232-73f7-4080-b15e-026c4c3e6df3")


async def save_decision(store: CheckpointStore, decision: VisualDecision) -> None:
    async def no_provider_calls():
        return decision
    saved = await store.json_stage("visual-decision", no_provider_calls, VisualDecision)
    if saved != decision:
        raise CheckpointError("Recovered checkpoint differs from validated fallback")


def recover(*, apply: bool) -> dict:
    settings = get_settings()
    with SessionLocal() as db:
        # Hold the task row lock until the checkpoint update is complete.
        task = db.get(Task, TASK_ID)
        if task is None or task.assigned_agent != AGENT:
            raise RuntimeError("Expected Whiteboard task does not exist")
        task = owner_task(db, task.product_id, TASK_ID, lock=True)
        source, state, approval, payload = records(db, task, lock=True)
        value = json.loads(state.content)
        if (task.status != TaskStatus.FAILED or approval.status != "approved"
                or value.get("failed_stage") != "visual_decision"
                or value.get("error") != "generation_failed"
                or value.get("calls") != call_limit(value, payload)
                or not payload.visual_review or payload.request.scenes != 6):
            raise RuntimeError("Job state or approval differs from the inspected failure; refusing to change it")
        if value.get("resumes", 0) and value.get("retry_at"):
            raise RuntimeError("Job has a pending retry; refusing concurrent checkpoint repair")

        directory = job_directory(settings.products_root, task.product_id, TASK_ID)
        store = CheckpointStore(directory / "stages", payload.request,
                                models=payload.models.model_dump(), visual_review=True,
                                image_provider="claudehub" if payload.image_provider == "claudehub" else None)
        storyboard = WhiteboardStoryboard.model_validate_json(store.load("storyboard", limit=64_000))
        review = VisualReview.model_validate_json(store.load("visual-review", limit=64_000))
        if len(storyboard.scenes) != 6:
            raise RuntimeError("Expected six complete storyboard scenes")
        review.check_for(storyboard)
        if len(review.issues) != 2 or sum(issue.eligible for issue in review.issues) != 1:
            raise RuntimeError("Critic report changed; manual review required")
        for index in range(6):
            data = store.load(f"scene-{index:02}-image")
            if not data.startswith(bytes.fromhex("89504e470d0a1a0a")):
                raise RuntimeError(f"Scene {index} has invalid PNG bytes")

        decision = VisualDecision(decisions=[
            IssueDecision(issue_id=issue.id, accept=False,
                          reason=VISUAL_DECISION_FALLBACK_REASON)
            for issue in review.issues
        ])
        decision.check_for(review)
        with store._lock():
            manifest = store._read_manifest()
        unfinished = {name for name, entry in manifest["stages"].items()
                      if entry.get("status") != "complete"}
        existing = manifest["stages"].get("visual-decision")
        if existing is None or unfinished not in ({"visual-decision"}, set()):
            raise RuntimeError("Unexpected unfinished stages; refusing to alter checkpoints")
        if existing.get("status") == "complete":
            stored = VisualDecision.model_validate_json(
                store.load("visual-decision", limit=64_000))
            if stored != decision:
                raise RuntimeError("Existing completed decision is not the expected fallback")
            action = "already_recovered"
        elif existing.get("status") == "interrupted_or_failed":
            action = "ready_to_recover"
            if apply:
                if store.reset_unfinished() != ["visual-decision"]:
                    raise RuntimeError("Unfinished stages changed while repairing")
                asyncio.run(save_decision(store, decision))
                action = "recovered_without_paid_calls"
        else:
            raise RuntimeError("Unexpected visual-decision status")
        # No state DB writes. The owner must explicitly extend the exhausted
        # budget in the normal site UI before the worker can resume.
        return {
            "task_id": str(TASK_ID),
            "action": action,
            "release_sha": settings.release_sha,
            "persisted_calls": value["calls"],
            "approved_limit": call_limit(value, payload),
            "original_images_verified": 6,
            "visual_issues": len(review.issues),
            "eligible_issues": sum(issue.eligible for issue in review.issues),
            "decision_redraws_authorized": 0,
            "remaining_regions_calls": len(storyboard.scenes),
            "remaining_speech_calls": sum(len(scene.beats) for scene in storyboard.scenes),
            "paid_calls_made": 0,
            "job_requeued": False,
            "owner_top_up_required": True,
            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write only the local validated decision checkpoint")
    args = parser.parse_args()
    print(json.dumps(recover(apply=args.apply), ensure_ascii=False, sort_keys=True))
