"""Read-only tenant-scoped production SEO/editorial diagnostics for one-off Actions run.

Output excludes prompts, article bodies, access tokens and customer data.
"""
import json
import re
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.domain import Product, Task, AgentRun, Artifact, Publication
from app.seo.activation import SeoFactoryActivation

def brief_error(text):
    if not text:
        return None
    # Never echo provider messages, URLs or company context; only internal codes/classes.
    m = re.match(r"^editorial_plan_failed:([A-Za-z0-9_:.,-]{1,180})", text)
    if m:
        return m.group(1)[:180]
    return "other_error_redacted"

def iso(value):
    return value.isoformat() if value else None

settings=get_settings()
print("FLAGS", json.dumps({
    "seo_autorun_enabled": settings.seo_autorun_enabled,
    "seo_servgo_autorun_enabled": settings.seo_servgo_autorun_enabled,
    "seo_interval_days": settings.seo_autorun_interval_days,
    "viral_research_mode": settings.viral_research_mode,
    "llm_provider_configured": bool(settings.llm_provider),
},ensure_ascii=False))
with SessionLocal() as db:
    products=list(db.scalars(select(Product).order_by(Product.created_at.desc()).limit(250)))
    matches=[]
    for p in products:
        name=(p.name or "").casefold().replace(" ","").replace("-","")
        slug=(p.slug or "").casefold().replace("-","")
        if slug=="servgo":
            matches.append(("ServGo",p))
        elif any(k in name or k in slug for k in ("proresultat","prorezultat","proresult","про результат","прорезультат")):
            matches.append(("ProResultat",p))
    print("TARGET_PRODUCT_COUNT",len(matches))
    print("TOTAL_PRODUCTS_SCANNED",len(products))
    for label,p in matches:
        print("TARGET",label,"slug",p.slug,"id",str(p.id))
        activation=db.get(SeoFactoryActivation,p.id)
        print("SEO_ACTIVATION",json.dumps({
            "company":label,
            "enabled":activation.enabled if activation is not None else None,
            "last_changed":iso(activation.changed_at) if activation is not None else None,
            "interval_days":activation.autorun_interval_days if activation is not None else None,
            "horizon_days":activation.autorun_horizon_days if activation is not None else None,
            "per_day":activation.publications_per_day if activation is not None else None,
        }))
        tasks=list(db.scalars(select(Task).where(Task.product_id==p.id,
            Task.assigned_agent.in_(["seo_factory","editorial_planner","content_factory"]))
            .order_by(Task.created_at.desc()).limit(100)))
        for agent in ("seo_factory","editorial_planner"):
            selected=[t for t in tasks if t.assigned_agent==agent][:5]
            print("AGENT_COUNT",label,agent,len([t for t in tasks if t.assigned_agent==agent]))
            for t in selected:
                print("TASK",json.dumps({
                    "company":label,"agent":agent,"id":str(t.id),"status":t.status.value,
                    "created":iso(t.created_at),"updated":iso(t.updated_at),
                    "due":iso(t.due_at),
                }))
                runs=list(db.scalars(select(AgentRun).where(AgentRun.task_id==t.id)
                        .order_by(AgentRun.started_at.desc()).limit(3)))
                for run in runs:
                    print("RUN",json.dumps({
                        "company":label,"agent":agent,"status":run.status,
                        "started":iso(run.started_at),"finished":iso(run.finished_at),
                        "error_category":brief_error(run.error) if agent=="editorial_planner" else bool(run.error),
                    }))
                if agent=="seo_factory":
                    artifacts=list(db.scalars(select(Artifact).where(
                        Artifact.product_id==p.id,Artifact.task_id==t.id,
                        Artifact.kind.in_(["seo_factory_report","seo_factory_stage","seo_content_plan","semantic_core","seo_factory_attempt"])
                        ).order_by(Artifact.created_at.desc()).limit(35)))
                    print("SEO_ARTIFACT_KINDS",json.dumps([a.kind for a in artifacts]))
                    for a in artifacts:
                        if a.kind not in ("seo_factory_report","seo_factory_stage"):
                            continue
                        try:
                            obj=json.loads(a.content)
                            if not isinstance(obj,dict):
                                continue
                        except (ValueError,TypeError):
                            continue
                        whitelist=("stage","error_code","plan_issue","http_status","retryable","articles_queued","articles_deferred","recorded_at")
                        out={k:obj[k] for k in whitelist if k in obj and type(obj[k]) in (str,int,bool,float)}
                        print("SEO_EVENT",json.dumps(out,ensure_ascii=False))
        articles=[t for t in tasks if t.assigned_agent=="content_factory"
                  and t.title.startswith("SEO-ARTICLE:")]
        print("SEO_ARTICLES",json.dumps([
            {"status":t.status.value,"due":iso(t.due_at)} for t in articles[:20]
        ]))
        plans=list(db.scalars(select(Artifact).where(Artifact.product_id==p.id,
            Artifact.kind=="editorial_plan").order_by(Artifact.created_at.desc()).limit(3)))
        print("EDITORIAL_PLANS",json.dumps([
            {"created":iso(a.created_at),"status":a.status.value} for a in plans
        ]))
        pubs=list(db.scalars(select(Publication).where(Publication.product_id==p.id,
            Publication.channel=="own_site").order_by(Publication.created_at.desc()).limit(20)))
        print("OWN_SITE_PUBLICATIONS",json.dumps([
            {"status":a.status,"created":iso(a.created_at),"published":iso(a.published_at)}
            for a in pubs
        ]))
