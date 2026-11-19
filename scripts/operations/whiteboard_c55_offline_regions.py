"""One-off local region fallback for c55 after repeated provider 502s.

Never contacts a provider, adds a budget, triggers a job, regenerates images,
or rewrites completed stages. Only runs while retry_wait and holds job.lock.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from app.content.whiteboard.checkpoints import CheckpointStore, _Lock
from app.content.whiteboard.contracts import WhiteboardStoryboard, WhiteboardRegionPlan
from app.content.whiteboard.jobs import AGENT, job_directory, owner_task, records
from app.content.whiteboard.layout import complete_diagram_regions
from app.content.whiteboard.review import VisualReview, VisualDecision
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import AgentRun, Task, TaskStatus

TASK_ID = UUID("c55fd232-73f7-4080-b15e-026c4c3e6df3")
AUTH = "owner-chat-explicit-2026-11-20-c55-plus19"


async def write_plan(store, name, plan):
    async def local():
        return plan
    actual = await store.json_stage(name, local, WhiteboardRegionPlan)
    if actual != plan:
        raise RuntimeError("Offline region plan did not round-trip unchanged")


def operate(apply=False):
    settings = get_settings()
    with SessionLocal() as db:
        task = db.get(Task, TASK_ID)
        if task is None or task.assigned_agent != AGENT:
            raise RuntimeError("Incorrect task")
        task = owner_task(db, task.product_id, TASK_ID, lock=True)
        source, state, approval, payload = records(db, task, lock=True)
        value = json.loads(state.content)
        cap = value.get("owner_chat_paid_attempt_cap") or {}
        if (cap.get("authorization") != AUTH or cap.get("max_additional") != 19 or
                cap.get("baseline_runs") != 40 or
                approval.status != "approved" or not payload.visual_review or
                task.status != TaskStatus.QUEUED or value.get("stage") != "retry_wait" or
                value.get("last_error") != "provider_server_error"):
            raise RuntimeError("Expected a safely paused approved c55 retry")
        begun = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.task_id == TASK_ID))
        if not 40 <= begun <= 59:
            raise RuntimeError("Owner-approved raw-attempt cap invalid")
        root = job_directory(settings.products_root, task.product_id, TASK_ID)
        with _Lock(root / "job.lock"):
            store = CheckpointStore(root / "stages", payload.request,
                models=payload.models.model_dump(), visual_review=True,
                image_provider="claudehub" if payload.image_provider == "claudehub" else None)
            storyboard = WhiteboardStoryboard.model_validate_json(store.load("storyboard", limit=64000))
            review = VisualReview.model_validate_json(store.load("visual-review", limit=64000))
            decision = VisualDecision.model_validate_json(store.load("visual-decision", limit=64000))
            review.check_for(storyboard)
            decision.check_for(review)
            if len(storyboard.scenes) != 6 or sum(len(s.beats) for s in storyboard.scenes) != 13:
                raise RuntimeError("Unexpected storyboard length")
            plans = {}
            with store._lock():
                manifest = store._read_manifest()
            failed = {key for key, st in manifest["stages"].items() if st.get("status") != "complete"}
            if any(not name.startswith("scene-") or not name.endswith("-regions") for name in failed):
                raise RuntimeError("Unexpected unfinished media stages")
            for index, scene in enumerate(storyboard.scenes):
                name = f"scene-{index:02}-regions"
                image = store.load(f"scene-{index:02}-image")
                if not image.startswith(bytes.fromhex("89504e470d0a1a0a")):
                    raise RuntimeError("Original PNG is invalid")
                entry = manifest["stages"].get(name)
                if entry and entry.get("status") == "complete":
                    plan = WhiteboardRegionPlan.model_validate_json(store.load(name, limit=64000))
                    plan.check_for(scene)
                    continue
                plan, audit = complete_diagram_regions(scene, image, None)
                plan.check_for(scene)
                plans[name] = (plan, audit["method"])
            report = {
                "task_id": str(TASK_ID), "status": "queued", "stage": "retry_wait",
                "paid_provider_calls": 0, "total_begun": begun,
                "additional_begun": begun - 40,
                "saved_original_images": 6, "plans_to_make_locally": len(plans),
                "methods": {name: method for name, (plan, method) in plans.items()},
                "human_review_required": True,
            }
            if not apply:
                report["action"] = "dry_run"
                return report
            if failed:
                deleted = set(store.reset_unfinished())
                if deleted != failed:
                    raise RuntimeError("Unexpected checkpoint change while recovering")
            for name, (plan, _) in plans.items():
                asyncio.run(write_plan(store, name, plan))
            value["c55_offline_region_fallback"] = {
                "source": "one_off_operator_after_region_502",
                "stage_names": sorted(plans),
                "human_review_required": True,
                "at": datetime.now(timezone.utc).isoformat(),
                "provider_calls": 0,
            }
            state.content = json.dumps(value, ensure_ascii=False)
            db.commit()
            report["action"] = "offline_region_checkpoints_completed"
            return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(operate(apply=args.apply), ensure_ascii=False, sort_keys=True))
