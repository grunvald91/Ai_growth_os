"""Read-only, tenant-scoped failure evidence for production SEO audits.

Never print raw exceptions, prompts, company content or provider response bodies.
The last recorded error is evidence, not proof of the current root cause.
"""
from collections import Counter
from uuid import UUID

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Artifact, Task, TaskStatus

STAGES = frozenset({
    "content_strategist", "seo_editor", "article_writer", "conversion_editor",
    "human_editor", "marketing_editor",
})
ASSEMBLY_STAGES = frozenset({
    "invalid_derivative_assembly", "invalid_marketing_editor_grounding",
    "invalid_article_assembly", "article_assembly",
})
ERROR_FAMILIES = frozenset({
    "assembly_failed", "invalid_article_output", "provider_request_failed",
    "context_preparation_failed", "provider_transient", "TimeoutError",
    "ReadTimeout", "ProviderEmptyResponseError", "ProviderMalformedResponseError",
})


def safe_error(error: str | None) -> tuple[str, str | None]:
    """Return allowlisted codes only, never arbitrary suffixes."""
    if not error:
        return "not_recorded", None
    parts = error.removeprefix("upstream_failed:").split(":")
    family = parts[0]
    if family not in ERROR_FAMILIES:
        return "unclassified", None
    stage = parts[1] if len(parts) > 1 else None
    return family, stage if stage in STAGES | ASSEMBLY_STAGES else None


def failure_report(db: Session, product_id: UUID, *, limit: int = 100) -> dict:
    """List failed SEO task evidence without writing or scheduling anything."""
    scope = (
        Task.product_id == product_id,
        Task.assigned_agent == "content_factory",
        Task.title.like("SEO-ARTICLE:%"),
        Task.status == TaskStatus.FAILED,
    )
    total = db.scalar(select(func.count()).select_from(Task).where(*scope)) or 0
    tasks = list(db.scalars(select(Task).where(*scope)
        .order_by(Task.updated_at.desc(), Task.id.desc())
        .limit(max(1, min(limit, 250)))))
    rows = []
    for task in tasks:
        runs = list(db.scalars(select(AgentRun).where(
            AgentRun.task_id == task.id,
        ).order_by(AgentRun.created_at.desc(), AgentRun.id.desc())))
        # Unstarted downstream agents may carry copies of the actual error.
        # Report the latest started run with an error; its timestamp may be
        # older than the latest attempt, so do not assert it caused this failure.
        latest = runs[0] if runs else None
        recorded = next((r for r in runs if r.error and r.started_at is not None
                         and r.status in {"failed", "completed"}), None)
        family, stage = safe_error(recorded.error if recorded else None)
        checkpoint_count = db.scalar(select(func.count()).select_from(Artifact).where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "content_stage_output",
        )) or 0
        article_exists = db.scalar(select(Artifact.id).where(
            Artifact.product_id == product_id, Artifact.task_id == task.id,
            Artifact.kind == "article",
        ).limit(1)) is not None
        rows.append({
            "task_id": str(task.id),
            "updated_at": task.updated_at.isoformat() if task.updated_at else None,
            "last_recorded_error_family": family,
            "last_recorded_error_stage": stage or (
                recorded.agent_name if recorded and recorded.agent_name in STAGES else None),
            "last_recorded_error_at": recorded.finished_at.isoformat()
                if recorded and recorded.finished_at else None,
            "latest_run_created_at": latest.created_at.isoformat()
                if latest and latest.created_at else None,
            "checkpoint_records": checkpoint_count,
            "article_exists": article_exists,
        })
    return {
        "failed_total": total,
        "reported": len(rows),
        "truncated": total > len(rows),
        "recorded_error_families": dict(Counter(r["last_recorded_error_family"] for r in rows)),
        "tasks": rows,
    }
