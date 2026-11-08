"""Guarded, idempotent one-shot production recovery after reviewed release.

Requeue only ServGo latest-plan failed items a0004/a0005; request exactly one
new ProResultat editorial plan unless another recent request already exists.
Requires the deployed application to contain verified retry code. No article
or content is printed and no draft is approved automatically.
"""
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Product, Task, TaskStatus, Artifact
from app.seo.article_retry import retry_failed_article
from app.content.calendar import queue_plan

SERVGO_RUN=UUID("2144a9c0-4e83-4e56-80b2-be61b3c738b3")
PRORESULTAT=UUID("ab4bdf82-0059-4197-8f08-0450f60c5dec")
EXPECTED_ITEMS={"a0004","a0005"}
CUTOFF=datetime(2026,11,8,tzinfo=timezone.utc)

with SessionLocal() as db:
    servgo=db.scalar(select(Product).where(Product.slug=="servgo"))
    target=db.get(Product,PRORESULTAT)
    if servgo is None or target is None or target.slug!="company-8a9d2b8354c3":
        raise SystemExit("PRECHECK_FAILED company identities changed")
    run=db.get(Task,SERVGO_RUN)
    if run is None or run.product_id!=servgo.id or run.status!=TaskStatus.COMPLETED:
        raise SystemExit("PRECHECK_FAILED latest ServGo factory run changed")
    last_run=db.scalar(select(Task.id).where(
        Task.product_id==servgo.id,Task.assigned_agent=="seo_factory",
    ).order_by(Task.created_at.desc()).limit(1))
    if last_run!=SERVGO_RUN:
        raise SystemExit("PRECHECK_FAILED newer SEO plan exists")
    plans=list(db.scalars(select(Artifact).where(
        Artifact.product_id==servgo.id,Artifact.task_id==run.id,
        Artifact.kind=="seo_content_plan")))
    if len(plans)!=1:
        raise SystemExit("PRECHECK_FAILED expected one SEO plan")
    plan_id=plans[0].id
    tasks=list(db.scalars(select(Task).where(
        Task.product_id==servgo.id,
        Task.title.like(f"SEO-ARTICLE:{plan_id}:%"),
        Task.assigned_agent=="content_factory",
    )))
    items={t.title.rsplit(":",1)[-1]:t for t in tasks}
    if len(items)!=5 or len(tasks)!=5 or not EXPECTED_ITEMS <= set(items):
        raise SystemExit("PRECHECK_FAILED SEO article count/identity changed")
    for item in EXPECTED_ITEMS:
        task=items[item]
        if task.status==TaskStatus.FAILED:
            existing=db.scalar(select(Artifact.id).where(
                Artifact.product_id==servgo.id,Artifact.task_id==task.id,
                Artifact.kind=="article"))
            if existing is not None:
                raise SystemExit("PRECHECK_FAILED failed task has article artifact")
        elif task.status not in (TaskStatus.QUEUED,TaskStatus.RUNNING,TaskStatus.COMPLETED):
            raise SystemExit("PRECHECK_FAILED unexpected article state")

    recent=list(db.scalars(select(Task).where(
        Task.product_id==target.id,Task.assigned_agent=="editorial_planner",
    ).order_by(Task.created_at.desc()).limit(2)))
    if not recent or recent[-1].created_at is None:
        raise SystemExit("PRECHECK_FAILED no ProResultat history")
    last=recent[0]
    last_created=last.created_at.replace(tzinfo=timezone.utc) if last.created_at.tzinfo is None else last.created_at
    existing_fresh=last_created>=CUTOFF
    if not existing_fresh:
        if last.status!=TaskStatus.COMPLETED:
            raise SystemExit("PRECHECK_FAILED unexpected existing planner state")
        last_plan=db.scalar(select(Artifact).where(
            Artifact.product_id==target.id,Artifact.task_id==last.id,
            Artifact.kind=="editorial_plan",
        ).order_by(Artifact.created_at.desc()).limit(1))
        if last_plan is None:
            raise SystemExit("PRECHECK_FAILED latest ProResultat planner lacks draft")
    print("PRECHECK_OK",{"servgo_failed_items":[i for i in sorted(EXPECTED_ITEMS) if items[i].status==TaskStatus.FAILED],
                         "proresultat_new_task_needed":not existing_fresh})
    for item in sorted(EXPECTED_ITEMS):
        if items[item].status==TaskStatus.FAILED:
            task=retry_failed_article(
                db, product_id=servgo.id,task_id=items[item].id,actor="github_actions_owner_recovery_20261108")
            print("SEO_ITEM_REQUEUED",item,str(task.id))
        else:
            print("SEO_ITEM_ALREADY_HANDLED",item,items[item].status.value)
    if existing_fresh:
        print("PRORESULTAT_RECENT_PLAN_ALREADY_REQUESTED",last.status.value)
    else:
        task=queue_plan(db,target.id)
        print("PRORESULTAT_PLAN_QUEUED",str(task.id),task.status.value)
print("RECOVERY_ACTIONS_COMPLETE")
# Real production rendering, without sending or exposing user content.
with SessionLocal() as db:
    from app.api.growth_live import seo_page
    from app.api.content_studio import plan_page
    servgo=db.scalar(select(Product).where(Product.slug=="servgo"))
    target=db.get(Product,PRORESULTAT)
    rendered=seo_page(servgo.id,db=db)
    seo_html=rendered.body.decode("utf-8")
    if "SEO-план и календарь" not in seo_html or "Плановый старт подготовки" not in seo_html:
        raise SystemExit("SEO_PRODUCTION_BOARD_FAILED")
    editorial_html=plan_page(db,target)
    if "Контент-план" not in editorial_html or "Следить за заданием" not in editorial_html:
        raise SystemExit("PRORESULTAT_PRODUCTION_PLANNER_UI_FAILED")
    print("SEO_PRODUCTION_BOARD_OK")
    print("PRORESULTAT_PRODUCTION_PLANNER_UI_OK")
