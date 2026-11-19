"""Read-only, privacy-minimized forensic summary of recent whiteboard jobs.

Execute inside the deployed ServGo Growth API container with an authenticated
operator-owned GitHub Actions runner. No provider calls and no database writes.
Prints only state/error metadata; never prompts, keys, media, or provider bodies.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Task, Artifact, AgentRun, Approval
from app.content.whiteboard.jobs import AGENT, job_directory

SAFE_ERROR_FIELDS = ("at", "stage", "code", "http_status", "exception_type", "calls", "refunded", "auto_resume")


def value_of(item):
    return getattr(item, "value", item)


def reduced_error(entry):
    return {key: entry[key] for key in SAFE_ERROR_FIELDS if key in entry}


def checkpoint_summary(directory: Path):
    path = directory / "stages" / "manifest.json"
    if not path.is_file():
        return {"available": False}
    try:
        # A manifest carries request details. Never emit the manifest itself.
        raw = json.loads(path.read_text(encoding="utf-8"))
        stages = raw.get("stages", {})
        if not isinstance(stages, dict):
            return {"available": True, "invalid": True}
        return {
            "available": True,
            "completed": sum(x.get("status") == "complete" for x in stages.values()),
            "unfinished": [
                {"stage": name, "status": x.get("status")}
                for name, x in stages.items() if x.get("status") != "complete"
            ][:10],
        }
    except (OSError, ValueError, TypeError):
        return {"available": True, "invalid": True}


def audit():
    settings = get_settings()
    print("whiteboard_runtime=", json.dumps({
        "release": settings.release_sha,
        "auto_resume_attempts": settings.whiteboard_auto_resume_attempts,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False))
    with SessionLocal() as db:
        tasks = list(db.scalars(
            select(Task).where(Task.assigned_agent == AGENT)
            .order_by(Task.created_at.desc(), Task.id.desc()).limit(8)
        ))
        print("whiteboard_tasks_found=", len(tasks))
        for task in tasks:
            states = list(db.scalars(
                select(Artifact).where(Artifact.task_id == task.id,
                    Artifact.kind.in_(("whiteboard_job_state", "whiteboard_request")))
            ))
            artifacts = {a.kind: a for a in states}
            try:
                state = json.loads(artifacts["whiteboard_job_state"].content)
            except (KeyError, ValueError, TypeError):
                state = {}
            try:
                approved_request = json.loads(artifacts["whiteboard_request"].content)
            except (KeyError, ValueError, TypeError):
                approved_request = {}
            errors = state.get("errors") if isinstance(state.get("errors"), list) else []
            runs = list(db.scalars(
                select(AgentRun).where(AgentRun.task_id == task.id)
            ))
            approvals = list(db.scalars(
                select(Approval).where(Approval.artifact_id == artifacts["whiteboard_request"].id)
            )) if "whiteboard_request" in artifacts else []
            total_calls = state.get("calls", 0)
            allowed = approved_request.get("max_api_calls")
            try:
                allowed = allowed + state.get("extra_calls", 0) if isinstance(allowed, int) else None
            except TypeError:
                allowed = None
            result = {
                "task_id": str(task.id), "status": str(value_of(task.status)),
                "created_at": task.created_at.isoformat() if task.created_at else None,
                "updated_at": task.updated_at.isoformat() if task.updated_at else None,
                "stage": state.get("stage"), "failed_stage": state.get("failed_stage"),
                "error": state.get("error"), "last_error": state.get("last_error"),
                "retry_at": state.get("retry_at"),
                "auto_resumes": state.get("auto_resumes", 0),
                "approved_call_limit": allowed, "recorded_calls": total_calls,
                "last_errors": [reduced_error(e) for e in errors[-12:] if isinstance(e, dict)],
                "error_count": len(errors),
                "attempt_run_counts": dict(Counter(str(value_of(r.status)) for r in runs)),
                "auto_approval_count": sum(a.action_type == "whiteboard_auto_resume" for a in approvals),
                "checkpoints": checkpoint_summary(job_directory(
                    settings.products_root, task.product_id, task.id)),
            }
            print("whiteboard_job=", json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    audit()
