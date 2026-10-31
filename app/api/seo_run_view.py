"""Read-only SEO factory run summaries over existing task/artifact records.

No worker state mutations, retry triggers, fabricated progress percentages or
claimed provider spending. Results remain scoped to the selected product.
"""
from __future__ import annotations

import json
from collections import defaultdict
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.orm import Session

from app.models.domain import AgentRun, Approval, Artifact, Publication, Task
from app.seo.publishing import reader_hash
from app.workflows.article_contracts import ArticleArtifact

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


def _critic_details(article: Artifact, review: Artifact | None) -> dict:
    """Only display a verdict for the exact final reader-facing article reviewed."""
    empty = {"critic_status": "missing", "critic_findings": []}
    if review is None:
        return empty
    data = _report_data(review.content)
    try:
        parsed = ArticleArtifact.model_validate_json(article.content)
        reader = parsed.reader_article
        if reader is None or data.get("reader_hash") != reader_hash(reader):
            return {"critic_status": "content_mismatch", "critic_findings": []}
    except (ValueError, TypeError):
        return {"critic_status": "content_mismatch", "critic_findings": []}
    status = data.get("status")
    findings = data.get("findings")
    issues = []
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            problem = finding.get("problem")
            if isinstance(problem, str) and problem.strip():
                issues.append(problem.strip()[:500])
    return {
        "critic_status": status if isinstance(status, str) and status else "missing",
        "critic_findings": issues[:3],
        "critic_findings_total": len(issues),
    }


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
        "articles": [],
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
    rows = [seo_run_summary(task, grouped[task.id]) for task in tasks]

    # The SEO factory queues article work for content_factory. Each task name
    # contains the generated plan artifact UUID (SEO-ARTICLE:<plan-uuid>:<item>).
    # Link it to the original run instead of assuming factory completion means
    # all articles are ready or published.
    plan_run = {
        str(a.id): a.task_id for a in parts
        if a.kind == "seo_content_plan" and a.task_id in task_ids
    }
    if not plan_run:
        return rows
    filters = [Task.title.like(f"SEO-ARTICLE:{plan_id}:%") for plan_id in plan_run]
    article_tasks = list(db.scalars(select(Task).where(
        Task.product_id == product_id, Task.assigned_agent == "content_factory",
        or_(*filters),
    ).order_by(Task.created_at, Task.id).limit(500)))
    if not article_tasks:
        return rows

    article_ids = [task.id for task in article_tasks]
    contents = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id,
        Artifact.task_id.in_(article_ids),
        Artifact.kind.in_(["article", "content_request"]),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc())))
    finished = {}
    requests = {}
    for artifact in contents:
        if artifact.kind == "article":
            finished.setdefault(artifact.task_id, artifact)
        elif artifact.kind == "content_request":
            requests.setdefault(artifact.task_id, artifact)

    completed_ids = [a.id for a in finished.values()]
    publications = list(db.scalars(select(Publication).where(
        Publication.product_id == product_id, Publication.channel == "own_site",
        Publication.status == "published",
        Publication.artifact_id.in_(completed_ids),
    ))) if completed_ids else []
    published = {p.artifact_id: p.external_reference for p in publications}

    # Batch-read latest final-editor runs and reviews. No model calls, changes
    # to article status, or hidden approval decisions during page rendering.
    final_runs = list(db.scalars(select(AgentRun).where(
        AgentRun.task_id.in_(article_ids),
        AgentRun.agent_name == "marketing_editor",
    ).order_by(AgentRun.created_at.desc(), AgentRun.id.desc())))
    latest_runs = {}
    for run in final_runs:
        latest_runs.setdefault(run.task_id, run.id)
    reviews = list(db.scalars(select(Artifact).where(
        Artifact.product_id == product_id, Artifact.kind == "seo_final_review",
        Artifact.agent_run_id.in_(list(latest_runs.values())),
    ).order_by(Artifact.created_at.desc(), Artifact.id.desc()))) if latest_runs else []
    review_by_run = {}
    for review in reviews:
        review_by_run.setdefault(review.agent_run_id, review)

    approvals = list(db.scalars(select(Approval).where(
        Approval.product_id == product_id,
        Approval.artifact_id.in_(completed_ids),
        Approval.requested_by == "seo_factory",
        Approval.action_type == "publish",
    ).order_by(Approval.created_at.desc(), Approval.id.desc()))) if completed_ids else []
    approval_by_article = {}
    for approval in approvals:
        approval_by_article.setdefault(approval.artifact_id, approval)
    by_run = defaultdict(list)
    for task in article_tasks:
        parts_of_title = task.title.split(":", 2)
        if len(parts_of_title) < 3 or parts_of_title[1] not in plan_run:
            continue
        article = finished.get(task.id)
        source = requests.get(task.id)
        request = _report_data(source.content) if source is not None else {}
        title = article.title if article else str(request.get("topic") or "Статья в очереди")
        state = task.status.value if hasattr(task.status, "value") else str(task.status)
        critic = _critic_details(article, review_by_run.get(latest_runs.get(task.id))) if article else {
            "critic_status": "not_ready", "critic_findings": [],
        }
        approval = approval_by_article.get(article.id) if article else None
        by_run[plan_run[parts_of_title[1]]].append({
            **critic,
            "approval_reason": approval.reason if approval and approval.status == "pending" else None,
            "needs_fact_check": bool(_report_data(article.content).get("fact_check_required")) if article else False,
            "task_id": str(task.id),
            "title": title,
            "task_status": state,
            "article_id": str(article.id) if article else None,
            "article_status": article.status.value if article else None,
            "publication_path": published.get(article.id) if article else None,
        })
    for row, task in zip(rows, tasks):
        row["articles"] = by_run.get(task.id, [])
    return rows
