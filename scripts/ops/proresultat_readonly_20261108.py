"""One-time read-only production verification of a missing ProResultat planner request.

Only categorical task, UI-message and account-scope metadata is printed.
No user messages, account identifiers, prompts or credentials are logged.
"""
from datetime import datetime, timezone
from collections import Counter
from urllib.parse import urlsplit
from sqlalchemy import select
from app.db.session import SessionLocal
from app.core.config import get_settings
from app.models.domain import Product, Task
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.sso.models import StudioBusinessLink

PID = "ab4bdf82-0059-4197-8f08-0450f60c5dec"
cutoff = datetime(2026, 11, 5, tzinfo=timezone.utc)
settings = get_settings()
print("STUDIO_ORIGIN_HOST",urlsplit(settings.studio_public_origin or "").hostname or "unset")
print("STUDIO_SERVGO_GATEWAY_ENABLED",bool(settings.studio_servgo_gateway_enabled))
with SessionLocal() as db:
    from uuid import UUID
    product = db.get(Product, UUID(PID))
    print("TARGET_EXISTS", bool(product))
    print("TARGET_NAME_MATCH", bool(product and "proresult" in product.name.lower().replace(" ","")))
    if not product:
        raise SystemExit(0)
    print("SSO_LINK_COUNT",len(list(db.scalars(select(StudioBusinessLink).where(
        StudioBusinessLink.product_id == product.id)))))
    convs = list(db.scalars(select(DispatcherConversation).where(
        DispatcherConversation.product_id == product.id)))
    print("DISPATCHER_CONVERSATIONS",len(convs))
    print("DISPATCHER_ACCOUNTS_DISTINCT",len({c.account for c in convs}))
    msgs = list(db.scalars(select(DispatcherMessage).where(
        DispatcherMessage.product_id == product.id,
        DispatcherMessage.created_at >= cutoff,
    ).order_by(DispatcherMessage.created_at.desc()).limit(160)))
    print("MESSAGES_SINCE_5_NOV",len(msgs))
    print("MESSAGE_CAPABILITIES",dict(Counter(m.capability for m in msgs
          if m.role=="assistant" and m.capability)))
    for msg in reversed(msgs[-55:]):
        if msg.role != "assistant":
            continue
        kind = "error" if any(t in msg.text.lower() for t in
             ("ошибка","не удалось","недоступ","не получилось","не могу")) else "reply"
        if msg.capability or msg.task_id or kind=="error":
            print("DISPATCHER_EVENT",{
                "at":msg.created_at.isoformat(),
                "kind":kind,
                "capability":msg.capability,
                "has_task":bool(msg.task_id),
                "is_plan_task":bool(msg.task_id and db.scalar(select(Task.id).where(
                    Task.id==msg.task_id, Task.product_id==product.id,
                    Task.assigned_agent=="editorial_planner"))),
            })
    tasks=list(db.scalars(select(Task).where(
        Task.product_id==product.id,Task.assigned_agent=="editorial_planner",
    ).order_by(Task.created_at.desc()).limit(6)))
    for t in tasks:
        print("PLANNER_TASK",{"created_at":t.created_at.isoformat(),"status":t.status.value,
                               "task_kind":"editorial_planner"})
try:
    from app.core.studio_accounts import load_accounts, account_scope
    accounts=load_accounts(settings)
    allowed_count=sum(1 for a in accounts.values()
                      if str(PID) in (account_scope(a) or []))
    print("LEGACY_ACCOUNTS_SCOPED_TO_PRODUCT",allowed_count)
except Exception as exc:
    print("ACCOUNT_SCOPING_CHECK",type(exc).__name__)
