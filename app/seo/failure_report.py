"""Read-only, tenant-scoped failure evidence for production SEO audits.

Never print raw exceptions, prompts, company content or provider response bodies.
The last recorded error is evidence, not proof of the current root cause.
"""
from collections import Counter
import json
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
    from app.seo.article_retry import recovery_blocker
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
            "automatic_recovery_blocker": recovery_blocker(
                db, product_id=product_id, task_id=task.id),
        })
    return {
        "failed_total": total,
        "reported": len(rows),
        "truncated": total > len(rows),
        "recorded_error_families": dict(Counter(r["last_recorded_error_family"] for r in rows)),
        "tasks": rows,
        "latest_planning_failure": latest_planning_failure(db, product_id),
    }


def latest_planning_failure(db: Session, product_id: UUID) -> dict | None:
    """Expose the last factory failure, never raw provider or customer data."""
    task = db.scalar(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "seo_factory",
    ).order_by(Task.created_at.desc(), Task.id.desc()).limit(1))
    if task is None or task.status != TaskStatus.FAILED:
        return None
    rows = db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.task_id == task.id,
        Artifact.kind == "seo_factory_report",
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()))
    allowed = {
        "stage": {"starting", "unknown", "prepare_queries", "collect_suggestions",
                  "landing_opportunities", "build_plan", "queue_articles", "completed"},
        "error_code": {"plan_validation_failed", "model_unusable_response",
            "provider_http_error", "upstream_timeout", "network_failure",
            "invalid_data_or_configuration", "internal_exception",
            "search_upstream_failures", "search_invalid_responses",
            "search_no_suggestions", "no_search_suggestions", "search_no_queries"},
        "plan_issue": {"unknown_cluster", "cluster_stage_intent_mismatch",
            "query_not_in_collected_clusters", "duplicate_or_previously_written_query",
            "no_fresh_clusters", "empty_company_profile", "other_plan_validation"},
    }
    for row in rows:
        try:
            value = json.loads(row.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict) or "error_code" not in value:
            continue
        result = {"task_id": str(task.id)}
        for key, choices in allowed.items():
            candidate = value.get(key)
            if isinstance(candidate, str):
                result[key] = candidate if candidate in choices else "unclassified"
        for key, low, high in (("attempt", 1, 100), ("http_status", 100, 599)):
            number = value.get(key)
            if type(number) is int and low <= number <= high:
                result[key] = number
        if type(value.get("retryable")) is bool:
            result["retryable"] = value["retryable"]
        return result
    return {"task_id": str(task.id), "error_code": "not_recorded"}
