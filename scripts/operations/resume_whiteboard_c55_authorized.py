"""One-off: apply the owner's explicit +19 *attempts* authorization to c55.

Executed exclusively by pinned production GitHub Actions after deploying the
CallLedger hard guard. --inspect is read-only. --apply is transactional and
idempotent. No paid API call is made by this script.
"""
from __future__ import annotations

import argparse
import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select

from app.content.whiteboard.checkpoints import CheckpointStore
from app.content.whiteboard.contracts import WhiteboardStoryboard
from app.content.whiteboard.jobs import (
    AGENT, RESUME_ACTION, TOP_UP_TOTAL, CallLedger, call_limit,
    job_directory, owner_task, records,
)
from app.content.whiteboard.review import VisualDecision, VisualReview, VISUAL_DECISION_FALLBACK_REASON
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Approval, ArtifactStatus, Product, Task, TaskStatus

TASK_ID = UUID("c55fd232-73f7-4080-b15e-026c4c3e6df3")
ADDITIONAL_PAID_ATTEMPTS = 19
EXPECTED_BUDGET = 34
EXPECTED_PRIOR_RUNS = 40
AUTHORIZATION = "owner-chat-explicit-2026-11-20-c55-plus19"


def validate_preserved_stages(settings, task, payload) -> dict:
    directory = job_directory(settings.products_root, task.product_id, TASK_ID)
    store = CheckpointStore(
        directory / "stages", payload.request,
        models=payload.models.model_dump(), visual_review=True,
        image_provider="claudehub" if payload.image_provider == "claudehub" else None,
    )
    with store._lock():
        manifest = store._read_manifest()
    expected = {"storyboard", "visual-review", "visual-decision"}
    expected.update(f"scene-{i:02}-image" for i in range(6))
    if set(manifest["stages"]) != expected or any(
        stage.get("status") != "complete" for stage in manifest["stages"].values()
    ):
        raise RuntimeError("Expected nine complete saved stages; refusing to restart anything")
    storyboard = WhiteboardStoryboard.model_validate_json(store.load("storyboard", limit=64_000))
    review = VisualReview.model_validate_json(store.load("visual-review", limit=64_000))
    decision = VisualDecision.model_validate_json(store.load("visual-decision", limit=64_000))
    if len(storyboard.scenes) != 6 or sum(len(s.beats) for s in storyboard.scenes) != 13:
        raise RuntimeError("Expected six scenes with 13 spoken beats")
    review.check_for(storyboard)
    decision.check_for(review)
    if (len(review.issues) != 2 or sum(issue.eligible for issue in review.issues) != 1
            or len(decision.decisions) != 2 or any(
                item.accept or item.reason != VISUAL_DECISION_FALLBACK_REASON
                for item in decision.decisions
            )):
        raise RuntimeError("The recovered visual-decision does not match the verified fallback")
    for index in range(6):
        original = store.load(f"scene-{index:02}-image")
        if not original.startswith(bytes.fromhex("89504e470d0a1a0a")):
            raise RuntimeError(f"Original image {index} has an invalid PNG header")
    output = directory / "output" / "video.mp4"
    if output.exists():
        raise RuntimeError("A video file already exists; do not risk overwriting it")
    return {"saved_stages": 9, "scenes": 6, "remaining_regions": 6,
            "remaining_speech": 13, "redraws_allowed": 0}


def verify_live_guard() -> None:
    if "owner_chat_paid_attempt_cap" not in inspect.getsource(CallLedger.begin):
        raise RuntimeError("Deployed API has no raw paid-attempt guard; refusing to authorize")


def operate(*, apply: bool) -> dict:
    verify_live_guard()
    settings = get_settings()
    with SessionLocal() as db:
        task = db.get(Task, TASK_ID)
        if task is None or task.assigned_agent != AGENT:
            raise RuntimeError("Unexpected task ID or type")
        db.scalar(select(Product).where(Product.id == task.product_id).with_for_update())
        task = owner_task(db, task.product_id, TASK_ID, lock=True)
        source, state, approval, payload = records(db, task, lock=True)
        value = json.loads(state.content)
        begun = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.task_id == TASK_ID))
        cap = value.get("owner_chat_paid_attempt_cap")
        summary = {
            "task_id": str(TASK_ID), "status": task.status.value,
            "stage": value.get("stage"), "calls": value.get("calls"),
            "current_approved_calls": call_limit(value, payload),
            "attempts_begun_total": begun,
            "extra_calls": value.get("extra_calls", 0),
            "release": settings.release_sha,
            "allowed_additional_paid_attempts": ADDITIONAL_PAID_ATTEMPTS,
        }
        if cap is not None:
            if (cap.get("authorization") != AUTHORIZATION or
                    cap.get("baseline_runs") != EXPECTED_PRIOR_RUNS or
                    cap.get("max_additional") != ADDITIONAL_PAID_ATTEMPTS or
                    call_limit(value, payload) != EXPECTED_BUDGET + ADDITIONAL_PAID_ATTEMPTS):
                raise RuntimeError("Existing rescue authorization does not match the approved limit")
            if begun - cap["baseline_runs"] > ADDITIONAL_PAID_ATTEMPTS:
                raise RuntimeError("Owner-approved raw call limit was exceeded")
            summary.update(action="already_authorized", new_attempts=begun - cap["baseline_runs"],
                           new_attempts_remaining=ADDITIONAL_PAID_ATTEMPTS - (begun - cap["baseline_runs"]))
            return summary

        if (task.status != TaskStatus.FAILED or approval.status != "approved"
                or source.status != ArtifactStatus.APPROVED
                or value.get("failed_stage") != "visual_decision"
                or value.get("error") != "generation_failed"
                or value.get("calls") != EXPECTED_BUDGET
                or call_limit(value, payload) != EXPECTED_BUDGET
                or begun != EXPECTED_PRIOR_RUNS
                or not payload.visual_review or payload.request.scenes != 6
                or payload.visual_repair_from is not None
                or int(value.get("extra_calls", 0)) + ADDITIONAL_PAID_ATTEMPTS > TOP_UP_TOTAL):
            raise RuntimeError("Unexpected owner-approved request/job history; refusing to grant extra calls")

        summary.update(validate_preserved_stages(settings, task, payload))
        if not apply:
            summary.update(action="ready_to_authorize", job_requeued=False)
            return summary

        # This single authorization is deliberately larger than the usual
        # 10-call button, but below the existing 25-call total top-up ceiling.
        # The 19-call *raw* cap is enforced independently inside CallLedger.begin.
        now = datetime.now(timezone.utc)
        grant = Approval(
            product_id=task.product_id, artifact_id=source.id,
            action_type=RESUME_ACTION, risk_level="YELLOW", status="approved",
            requested_by="owner", decided_by=AUTHORIZATION, decided_at=now,
            reason=(f"Explicit owner chat authorization: at most {ADDITIONAL_PAID_ATTEMPTS} "
                    f"additional begun paid API calls for task {TASK_ID}; "
                    f"baseline {begun} AgentRun records, no prior-stage regeneration"),
        )
        db.add(grant)
        db.flush()
        value.update(
            extra_calls=int(value.get("extra_calls", 0)) + ADDITIONAL_PAID_ATTEMPTS,
            owner_chat_paid_attempt_cap={
                "authorization": AUTHORIZATION,
                "approval_id": str(grant.id),
                "baseline_runs": begun,
                "max_additional": ADDITIONAL_PAID_ATTEMPTS,
            },
            stage="queued", error=None, failed_stage=None, diagnostic=None,
            retry_at=None, resume_requested=True,
            resumes=int(value.get("resumes", 0)) + 1,
            auto_resumes=0,
        )
        state.content = json.dumps(value, ensure_ascii=False)
        task.status = TaskStatus.QUEUED
        db.commit()
        summary.update(action="queued_with_hard_19_attempt_cap",
                       new_approved_limit=EXPECTED_BUDGET + ADDITIONAL_PAID_ATTEMPTS,
                       grant_id=str(grant.id), job_requeued=True)
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(operate(apply=args.apply), ensure_ascii=False, sort_keys=True))
