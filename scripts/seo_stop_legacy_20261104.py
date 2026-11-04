"""One-time guarded ServGo SEO safety stop. No content deletion or other tenants affected."""
import json
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus, Artifact, Publication
from app.seo.activation import is_enabled, set_enabled
from app.workflows.article_contracts import ArticleProductionInput

OLD = frozenset({
    "записаться на маникюр онлайн",
    "онлайн запись к косметологу",
    "запись к психологу",
    "запись на консультацию",
    "онлайн запись на услуги",
})
with SessionLocal() as db:
    product = db.scalar(select(Product).where(Product.slug == "servgo").with_for_update())
    if product is None or product.slug != "servgo":
        raise SystemExit("STOP_ABORTED: exact ServGo company not found")
    jobs = list(db.scalars(select(Task).where(
        Task.product_id == product.id, Task.assigned_agent == "content_factory",
        Task.title.like("SEO-ARTICLE:%"),
        Task.status.in_([TaskStatus.QUEUED, TaskStatus.RUNNING]))))
    old = []
    for job in jobs:
        request = db.scalar(select(Artifact).where(
            Artifact.product_id == product.id, Artifact.task_id == job.id,
            Artifact.kind == "content_request").limit(1))
        if request is None:
            raise SystemExit("STOP_ABORTED: request missing, manual inspection needed")
        data = ArticleProductionInput.model_validate_json(request.content)
        query = data.seo_brief.primary_query if data.seo_brief else None
        if query not in OLD:
            raise SystemExit("STOP_ABORTED: non-legacy SEO job found; no changes made")
        old.append((job, query))
    before = is_enabled(db, product.id)
    if old and before:
        set_enabled(db, product.id, enabled=False, actor="seo-buyer-intent-safety-20261104")
    db.expire_all()
    pending = sum(1 for task, _ in old if db.get(Task, task.id).status == TaskStatus.QUEUED)
    running = sum(1 for task, _ in old if db.get(Task, task.id).status == TaskStatus.RUNNING)
    cancelled = sum(1 for task, _ in old if db.get(Task, task.id).status == TaskStatus.CANCELLED)
    out = {"company": "servgo", "was_enabled": before, "enabled_after": is_enabled(db, product.id),
           "old_tasks_inspected": len(old), "queued_after": pending,
           "running_after": running, "cancelled_after": cancelled}
    print("SEO_SAFETY_STOP " + json.dumps(out, ensure_ascii=False, sort_keys=True), flush=True)
