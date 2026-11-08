"""Read-only verification of latest ServGo SEO plan articles and ProResultat requests."""
import json
import re
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.domain import Product, Task, Artifact, AgentRun, Publication
def stamp(v):
    return v.isoformat() if v else None
def kind(v):
    # AgentRun.error contains an internal diagnostic prefix; do not echo free text.
    return ":".join(re.findall(r"[A-Za-z_][A-Za-z0-9_]{1,49}",(v or "").splitlines()[0])[:4]) if v else None
with SessionLocal() as db:
    servgo=db.scalar(select(Product).where(Product.slug=="servgo"))
    if servgo is None:
        print("SERVGO_NOT_FOUND")
    else:
        factory=db.scalar(select(Task).where(Task.product_id==servgo.id,
            Task.assigned_agent=="seo_factory").order_by(Task.created_at.desc()).limit(1))
        if factory is None:
            print("LATEST_FACTORY_MISSING")
        else:
            plans=list(db.scalars(select(Artifact).where(
                Artifact.product_id==servgo.id,Artifact.task_id==factory.id,
                Artifact.kind=="seo_content_plan")))
            print("LATEST_SEO_PLAN_COUNT",len(plans))
            for plan in plans:
                tasks=list(db.scalars(select(Task).where(
                    Task.product_id==servgo.id,
                    Task.title.like("SEO-ARTICLE:"+str(plan.id)+":%")
                    ).order_by(Task.created_at).limit(100)))
                print("LATEST_SEO_ARTICLE_TASK_COUNT",len(tasks))
                for t in tasks:
                    runs=list(db.scalars(select(AgentRun).where(AgentRun.task_id==t.id)
                            .order_by(AgentRun.started_at.desc()).limit(3)))
                    arts=list(db.scalars(select(Artifact).where(
                        Artifact.product_id==servgo.id,Artifact.task_id==t.id,
                        Artifact.kind.in_(["article","seo_final_review"]))
                        .order_by(Artifact.created_at.desc()).limit(20)))
                    article_ids=[a.id for a in arts if a.kind=="article"]
                    pubs=list(db.scalars(select(Publication).where(
                        Publication.product_id==servgo.id,
                        Publication.artifact_id.in_(article_ids)))) if article_ids else []
                    strategist=next((r for r in runs if r.agent_name=="content_strategist"),None)
                    if strategist is None:
                        strategist=db.scalar(select(AgentRun).where(
                            AgentRun.task_id==t.id,AgentRun.agent_name=="content_strategist"
                        ).order_by(AgentRun.started_at.desc()).limit(1))
                    if strategist and strategist.error:
                        print("STRATEGIST_ERROR",json.dumps({
                            "item":t.title.rsplit(":",1)[-1],
                            "reason":re.sub(r"[^A-Za-z0-9_:.,-]","_",strategist.error)[:280],
                        }))
                    print("LATEST_SEO_ARTICLE",json.dumps({
                        "item":t.title.rsplit(":",1)[-1],"task_status":t.status.value,
                        "due":stamp(t.due_at),"article_ready":bool(article_ids),
                        "publication_statuses":[p.status for p in pubs],
                        "latest_run_statuses":[r.status for r in runs],
                        "latest_error_categories":[kind(r.error) for r in runs if r.error],
                    }))
