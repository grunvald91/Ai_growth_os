"""Ask the studio dispatcher a list of owner questions on the production server and print how it answers.

Runs INSIDE the studio api container (`docker exec -i ai-growth-os-api-1 python -`), with the container's own
settings, model key and ServGo link. Nothing is published or created for the owner:
* only read capabilities are executed; anything else is printed as «WOULD_RUN» and not carried out;
* if business 11 has no company in the studio yet, a temporary one is created and deleted again at the end
  (together with everything the probe wrote for it); an existing company is only used, its conversation of the
  probe account is removed afterwards;
* table cells (client names, phones) are never printed, only table sizes; the cells of the business-summary and
  decision tables hold no client data and are printed;
* with a temporary company the probe also lets the team work: the hand-over to the marketing team is carried out and
  its review is awaited (the production worker runs it), then everything is deleted again.
"""
import asyncio
import json
import time
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, func, select

import app.models  # noqa: F401  registers every table
from app.api.dispatcher import get_dispatcher_llm
from app.core.config import get_settings
from app.db.base import Base
from app.db.session import SessionLocal
from app.dispatcher import service
from app.dispatcher.executors import Context
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.dispatcher.outcome import Outcome
from app.models.domain import CostRecord, Product
from app.sso.models import StudioBusinessLink

BUSINESS_ID = 11
ACCOUNT = "probe-claude"
READ_ONLY = {"show_schedule", "show_work", "show_review", "show_plan", "go_to", "show_material", "show_insights"}
TEAM = {"set_insights_depth", "send_to_team"}  # change data of the company: only for the temporary one
OPEN_TABLES = ("Показатель", "Решение")
WAIT_SECONDS = 420
QUESTIONS = [
    "Сколько отмен за последнее время?",
    "Часто ли клиенты возвращаются?",
    "В какие дни недели у меня пустуют яхты?",
]


def summary(blocks):
    parts = []
    for block in blocks or []:
        kind = block.get("type")
        if kind == "table":
            columns = block.get("columns") or []
            parts.append(f"table {len(block.get('rows') or [])}x{len(columns)} cols={columns}")
            if columns and columns[0] in OPEN_TABLES:
                parts.extend("   | " + " | ".join(str(cell) for cell in row) for row in block.get("rows") or [])
        elif kind == "chips":
            parts.append("chips=" + " | ".join(str(i.get("label")) for i in block.get("items") or []))
        elif kind != "text":
            parts.append(str(kind))
    return "; ".join(parts) or "-"


async def team_result(db, ctx, product):
    """Wait for the marketing team's review of the handed-over facts and print what comes back into the chat."""
    from app.dispatcher import updates
    from app.dispatcher.service import open_conversation
    from app.insights.worker import AGENT
    from app.models.domain import Decision, Metric, Task
    metrics = db.scalar(select(func.count()).select_from(Metric).where(Metric.product_id == product.id))
    print(f"\nmetrics_stored={metrics}")
    task = db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == AGENT))
    if task is None:
        print("no hand-over task was queued")
        return
    for waited in range(0, WAIT_SECONDS, 10):
        db.expire_all()
        task = db.get(Task, task.id)
        if task.status.value in ("completed", "failed"):
            break
        await asyncio.sleep(10)
    print(f"team_task_status={task.status.value} waited_about={waited}s")
    added = updates.collect_updates(db, ctx, open_conversation(db, product.id, ctx.account))
    for message in added:
        print(f"   RESULT CARD: {message.text}")
        print(f"   blocks: {summary(message.blocks)}")
    for decision in db.scalars(select(Decision).where(Decision.product_id == product.id)):
        print(f"   decision {decision.decision_type.value}: evidence_refs={len(decision.evidence_references or [])}")


async def main():
    settings = get_settings()
    llm = get_dispatcher_llm(settings)
    if llm is None:
        print("no model configured; stop")
        return
    original = service.act
    created_holder = {"yes": False}

    async def guarded(ctx, capability_id, raw_params):
        if capability_id not in READ_ONLY and not (created_holder["yes"] and capability_id in TEAM):
            print(f"   WOULD_RUN {capability_id} {json.dumps(raw_params, ensure_ascii=False)[:200]}")
            return Outcome(text="[проба: действие не выполнялось]"), None, capability_id
        return await original(ctx, capability_id, raw_params)

    service.act = guarded
    plain_generate = llm.generate

    async def spy(request, **kwargs):
        """Shows what the model sent back for each question (its decision, no secrets) and how it failed."""
        started = time.monotonic()
        try:
            reply = await plain_generate(request, **kwargs)
        except BaseException as error:  # noqa: BLE001 - a cancelled wait (timeout) must be visible too
            print(f"   LLM_ERROR {type(error).__name__} after {time.monotonic() - started:.0f}s: {str(error)[:200]}")
            raise
        print(f"   LLM answered in {time.monotonic() - started:.0f}s")
        usage = reply.usage
        print(f"   LLM stop={reply.stop_reason} tokens_out={getattr(usage, 'output_tokens', None)} "
              f"chars={len(reply.content)} head={reply.content[:240]!r}")
        return reply

    llm.generate = spy
    db = SessionLocal()
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == BUSINESS_ID))
    created = False
    if link is not None:
        product = db.get(Product, link.product_id)
        print(f"company_exists=yes slug_enabled={product.slug in settings.dispatcher_enabled_slugs.split(',')}")
    else:
        product = Product(name="Проба диспетчера: заведение 11", slug="probe-" + uuid4().hex[:10],
                          description="Временная компания для пробы диспетчера; будет удалена.")
        db.add(product)
        db.flush()
        db.add(StudioBusinessLink(servgo_business_id=BUSINESS_ID, product_id=product.id))
        db.commit()
        created = True
        created_holder["yes"] = True
        print("company_exists=no; temporary company created")
    ctx = Context(db=db, product=product, settings=settings, account=ACCOUNT)
    try:
        for number, question in enumerate(QUESTIONS, start=1):
            print(f"\n[{number}] {question}")
            try:
                result = await service.handle_message(db, ctx, llm, text=question, client_key=None)
            except Exception as error:  # noqa: BLE001 - the probe must go on with the next question
                print(f"   ERROR {type(error).__name__}")
                db.rollback()
                continue
            answer = next((m for m in result["messages"] if m["role"] == "assistant"), None)
            row = db.scalar(select(DispatcherMessage).where(DispatcherMessage.id == answer["id"])) if answer else None
            if row is None:
                print("   no answer")
                continue
            print(f"   intent={row.intent} capability={row.capability} confidence={row.confidence} model={row.model}")
            print(f"   params={json.dumps(row.params, ensure_ascii=False)[:200]}")
            print(f"   reply: {row.text}")
            print(f"   blocks: {summary(row.blocks)}")
        if created:
            await team_result(db, ctx, product)
        spent = db.scalar(select(func.coalesce(func.sum(CostRecord.amount), 0)).where(CostRecord.product_id == product.id))
        calls = db.scalar(select(func.count()).select_from(CostRecord).where(CostRecord.product_id == product.id))
        print(f"\nmodel_calls={calls} cost_usd={Decimal(spent):.4f}")
    finally:
        service.act = original
        db.rollback()
        if created:
            from app.models.domain import Task
            own_tasks = select(Task.id).where(Task.product_id == product.id)
            for table in reversed(Base.metadata.sorted_tables):  # children before parents
                if "product_id" in table.c:
                    db.execute(delete(table).where(table.c.product_id == product.id))
                elif "task_id" in table.c:  # for example the agent runs: they belong to the company through the task
                    db.execute(delete(table).where(table.c.task_id.in_(own_tasks)))
            db.execute(delete(Product).where(Product.id == product.id))
            db.commit()
            print("temporary company and everything the probe wrote for it: deleted")
        else:
            conversations = db.scalars(select(DispatcherConversation.id).where(
                DispatcherConversation.product_id == product.id, DispatcherConversation.account == ACCOUNT)).all()
            if conversations:
                db.execute(delete(DispatcherMessage).where(DispatcherMessage.conversation_id.in_(conversations)))
                db.execute(delete(DispatcherConversation).where(DispatcherConversation.id.in_(conversations)))
            db.commit()
            print("probe conversation deleted")


asyncio.run(main())
