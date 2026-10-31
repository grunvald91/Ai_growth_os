"""Read-only SEO factory run summaries over existing task/artifact records.

No worker state mutations, retry triggers, fabricated progress percentages or
claimed provider spending. Results remain scoped to the selected product.
"""
from __future__ import annotations

import json
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.domain import Artifact, Task

SEO_AGENT = "seo_factory"
SEO_KINDS = (
    "seo_factory_report", "seo_factory_request", "seo_factory_attempt",
    "semantic_core", "seo_content_plan",
)
SEO_STATUS_NAMES = {
    "queued": "В очереди",
    "running": "Выполняется",
    "waiting_approval": "Ожидает согласования",
    "completed": "Завершён",
    "failed": "Ошибка",
    "cancelled": "Отменён",
}


def _report_data(content: str) -> dict:
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def seo_run_summary(task: Task, artifacts: list[Artifact]) -> dict:
    kinds = {a.kind for a in artifacts}
    reports = sorted((a for a in artifacts if a.kind == "seo_factory_report"),
                     key=lambda a: a.created_at, reverse=True)
    result_reports = [_report_data(a.content) for a in reports]
    success = next((r for r in result_reports if isinstance(r.get("articles_queued"), int)
                    and r["articles_queued"] >= 0), None)
    # The last error of a currently failed/requeued task is an observation,
    # not a claim about the next retry's result.
    error = next((r.get("error") for r in result_reports
                  if isinstance(r.get("error"), str)), None)
    state = task.status.value if hasattr(task.status, "value") else str(task.status)
    return {
        "id": str(task.id),
        "title": task.title,
        "status": state,
        "status_label": SEO_STATUS_NAMES.get(state, state),
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "attempts": sum(a.kind == "seo_factory_attempt" for a in artifacts),
        "semantic_ready": "semantic_core" in kinds,
        "plan_ready": "seo_content_plan" in kinds,
        "articles_queued": success["articles_queued"] if success is not None else None,
        "sources_confirmed": (len(success["sources"])
                              if success is not None and isinstance(success.get("sources"), list)
                              else None),
        "last_error": error,
        # Task/AgentRun currently have no authoritative provider billing value.
        "cost_amount": None,
        "cost_status": "not_recorded",
    }


def seo_runs(db: Session, product_id: UUID, *, limit: int = 20) -> list[dict]:
    tasks = list(db.scalars(
        select(Task).where(Task.product_id == product_id,
                           Task.assigned_agent == SEO_AGENT)
        .order_by(Task.created_at.desc(), Task.id.desc()).limit(limit)
    ))
    if not tasks:
        return []
    task_ids = [task.id for task in tasks]
    parts = list(db.scalars(
        select(Artifact).where(
            Artifact.product_id == product_id,
            Artifact.task_id.in_(task_ids),
            Artifact.kind.in_(SEO_KINDS),
        ).order_by(Artifact.created_at.desc()).limit(500)
    ))
    grouped = defaultdict(list)
    for artifact in parts:
        grouped[artifact.task_id].append(artifact)
    return [seo_run_summary(task, grouped[task.id]) for task in tasks]
