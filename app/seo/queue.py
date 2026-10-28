"""Turns approved plan items into article tasks for the existing content worker."""
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content.service import queue_content
from app.models.domain import Artifact, Task
from app.seo.contracts import PlanItem, SeoContentPlan
from app.seo.planner import PLAN_KIND
from app.workflows.article_contracts import ArticleProductionInput, SeoBrief

SEO_TASK_PREFIX = "SEO-ARTICLE:"


def brief_for(item: PlanItem, related: list[str]) -> SeoBrief:
    return SeoBrief(plan_item_id=item.id, primary_query=item.primary_query, related_queries=related,
                    stage=item.stage, intent=item.intent, reader_problem=item.reader_problem,
                    ai_questions=item.ai_questions, next_step=item.next_step)


def queue_plan_item(db: Session, product_id: UUID, plan_artifact_id: UUID, item: PlanItem, related: list[str]) -> Task:
    """One task per plan item. The key makes repeats return the same task, never a duplicate article."""
    brief = brief_for(item, related)
    task = queue_content(db, product_id, item.working_title, commit=False,
                         dedupe_key=f"{SEO_TASK_PREFIX}{plan_artifact_id}:{item.id}",
                         audience=None, editorial_brief=item.reader_problem, format="package")
    # queue_content built a package request; replace it with the own-site SEO request carrying the brief.
    request = db.scalar(select(Artifact).where(Artifact.task_id == task.id, Artifact.kind == "content_request"))
    data = json.loads(request.content)
    if data.get("seo_brief") is None:
        data.update(channel="own_site", produce_ready_assets=False, request_publication=False,
                    seo_brief=brief.model_dump(mode="json"))
        request.content = ArticleProductionInput.model_validate(data).model_dump_json()
    db.commit()
    return task


def queue_plan(db: Session, plan_artifact: Artifact, clusters_by_id: dict, *, only: set[str] | None = None) -> list[Task]:
    plan = SeoContentPlan.model_validate_json(plan_artifact.content)
    tasks = []
    for item in plan.items:
        if only is not None and item.id not in only:
            continue
        related = list(dict.fromkeys(q for cid in item.cluster_ids for q in clusters_by_id[cid].queries
                                     if q != item.primary_query))
        tasks.append(queue_plan_item(db, plan_artifact.product_id, plan_artifact.id, item, related))
    return tasks
