"""Read-only post-recovery status of the five ServGo SEO jobs and ProResultat planner."""
from uuid import UUID
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Product, Task, Artifact, AgentRun, Publication

factory=UUID("2144a9c0-4e83-4e56-80b2-be61b3c738b3")
pro=UUID("ab4bdf82-0059-4197-8f08-0450f60c5dec")
expected_planner=UUID("25e9e11c-1d9d-4579-8603-1abfad7326ef")
with SessionLocal() as db:
    sg=db.scalar(select(Product).where(Product.slug=="servgo"))
    if sg is None:
        raise SystemExit("SERVGO_NOT_FOUND")
    plan=db.scalar(select(Artifact).where(Artifact.product_id==sg.id,
        Artifact.task_id==factory, Artifact.kind=="seo_content_plan"))
    if plan is None:
        raise SystemExit("SERVGO_SEO_PLAN_MISSING")
    tasks=list(db.scalars(select(Task).where(Task.product_id==sg.id,
        Task.assigned_agent=="content_factory",
        Task.title.like("SEO-ARTICLE:"+str(plan.id)+":%")).order_by(Task.title)))
    for t in tasks:
        item=t.title.rsplit(":",1)[-1]
        article=db.scalar(select(Artifact.id).where(
            Artifact.product_id==sg.id,Artifact.task_id==t.id,
            Artifact.kind=="article").limit(1))
        published=db.scalar(select(Publication.status).where(
            Publication.product_id==sg.id,
            Publication.artifact_id==article,
            Publication.channel=="own_site"
        ).limit(1)) if article else None
        latest=db.scalar(select(AgentRun).where(AgentRun.task_id==t.id,
            AgentRun.agent_name=="content_strategist"
        ).order_by(AgentRun.created_at.desc()).limit(1))
        print("SEO_ITEM_STATUS",item,t.status.value,"article_exists",bool(article),
              "publication",published,"latest_strategist_status",latest.status if latest else "none",
              "due",t.due_at.isoformat() if t.due_at else "none")
    task=db.get(Task,expected_planner)
    if task is None or task.product_id!=pro:
        raise SystemExit("PRORESULTAT_NEW_PLANNER_MISSING")
    output=db.scalar(select(Artifact).where(Artifact.product_id==pro,
        Artifact.task_id==task.id,Artifact.kind=="editorial_plan")
        .order_by(Artifact.created_at.desc()).limit(1))
    import json
    content=json.loads(output.content) if output else {}
    print("PRORESULTAT_NEW_PLANNER_STATUS",task.status.value,
          "draft_exists",bool(output),"approval_state",content.get("approval_state"),
          "slot_count",len(content.get("slots") or []))
    from app.core.config import get_settings
    from app.seo.queue import articles_started_today, quota_exhausted
    from app.models.domain import TaskStatus
    settings=get_settings()
    quota=settings.seo_articles_per_day
    started=articles_started_today(db,product_id=sg.id)
    blocked=quota_exhausted(db,quota,product_id=sg.id)
    pending=db.scalars(select(Task).where(Task.status==TaskStatus.QUEUED,
        Task.assigned_agent.in_(["content_factory","editorial_planner","seo_factory"]))
        .order_by(Task.priority.desc(), Task.created_at).limit(10))
    print("SEO_QUOTA",{"daily_start_limit":quota,"today_starts":started,"blocked_by_daily_limit":blocked})
    print("WORKER_QUEUE_HEAD",[(t.assigned_agent,t.status.value,
        "servgo" if t.product_id==sg.id else "proresultat" if t.product_id==pro else "other")
        for t in pending])
    print("READONLY_STATUS_OK")
