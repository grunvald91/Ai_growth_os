"""One dispatcher turn: understand the request, check policy in code, act, answer, record.

A message either arrives as a hint chip (a complete request, no model call) or as free text
(the model returns a structured decision). The server never trusts the model for permission.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dispatcher import executors
from app.dispatcher.blocks import Chip, clean_blocks
from app.dispatcher.decision import (CONFIDENCE_TO_ACT, SYSTEM_PROMPT, DispatcherDecision, build_user_message)
from app.dispatcher.executors import Confirmation, Context, Outcome
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.dispatcher.policy import Verdict, verdict
from app.dispatcher.readers import snapshot
from app.dispatcher.registry import CAPABILITIES
from app.llm.costing import usage_cost_details
from app.llm.lenient import relax
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.models.domain import CostRecord, ToolCall
from app.tools import ToolOperation

_logger = logging.getLogger(__name__)

# The owner should not stare at a spinner: after this the dispatcher answers with hints and links.
DECISION_TIMEOUT_SECONDS = 45

FALLBACK_TEXT = ("Не получилось разобрать просьбу — попробуйте сказать иначе или выберите готовый вариант ниже.")


# ---- conversation -----------------------------------------------------------------------

def open_conversation(db: Session, product_id: UUID, account: str, *, fresh: bool = False) -> DispatcherConversation:
    """The account's current thread; `fresh` starts a new topic and closes the old one."""
    current = db.scalar(select(DispatcherConversation).where(
        DispatcherConversation.product_id == product_id, DispatcherConversation.account == account,
        DispatcherConversation.status == "open").order_by(DispatcherConversation.created_at.desc()).limit(1))
    if current is not None and not fresh:
        return current
    if current is not None:
        current.status = "closed"
    created = DispatcherConversation(product_id=product_id, account=account)
    db.add(created)
    db.commit()
    return created


def messages(db: Session, conversation: DispatcherConversation) -> list[DispatcherMessage]:
    return list(db.scalars(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id).order_by(DispatcherMessage.seq)))


def to_dict(message: DispatcherMessage) -> dict:
    return {"id": str(message.id), "seq": message.seq, "role": message.role, "mode": message.input_mode,
            "text": message.text, "spoken": message.spoken, "blocks": message.blocks, "intent": message.intent,
            "created_at": message.created_at.isoformat() if message.created_at else None}


def _next_seq(db: Session, conversation: DispatcherConversation) -> int:
    return (db.scalar(select(func.max(DispatcherMessage.seq)).where(
        DispatcherMessage.conversation_id == conversation.id)) or 0) + 1


def _add(db: Session, conversation: DispatcherConversation, **values) -> DispatcherMessage:
    message = DispatcherMessage(conversation_id=conversation.id, product_id=conversation.product_id,
                                seq=_next_seq(db, conversation), **values)
    db.add(message)
    db.flush()
    return message


def _add_committed(db: Session, conversation: DispatcherConversation, **values) -> DispatcherMessage:
    """Store and commit one message. Two tabs or phones writing at once can pick the same number;
    the loser takes the next one instead of failing (the work it reports is already done)."""
    for attempt in range(5):
        try:
            message = _add(db, conversation, **values)
            db.commit()
            return message
        except IntegrityError:
            db.rollback()
            if values.get("client_key") and db.scalar(select(DispatcherMessage.id).where(
                    DispatcherMessage.conversation_id == conversation.id,
                    DispatcherMessage.client_key == values["client_key"])):
                raise  # A real repeat of the same message, not a numbering clash.
            if attempt == 4:
                raise
    raise RuntimeError("unreachable")


# ---- model ------------------------------------------------------------------------------

def record_llm_cost(db: Session, product_id: UUID, response, provider: str) -> None:
    usage = response.usage
    db.add(CostRecord(
        product_id=product_id, category="llm", provider=provider, amount=0, currency="USD",
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        incurred_at=datetime.now(timezone.utc),
        details={**usage_cost_details(response), "task_type": "dispatcher", "model": response.model,
                 "usage_incomplete": usage is None},
    ))
    db.commit()


async def ask_model(db: Session, llm: LLMService, settings, ctx: Context, history: list[DispatcherMessage],
                    text: str) -> tuple[DispatcherDecision | None, str | None]:
    """(decision, model name). A model failure or unreadable answer gives (None, ...)."""
    turns = [LLMMessage(role=m.role, content=m.text) for m in history if m.text]
    request = LLMRequest(
        system_prompt=SYSTEM_PROMPT,
        messages=[*turns, LLMMessage(role="user", content=build_user_message(snapshot(ctx.db, ctx.product), text))],
        model=settings.llm_model, max_tokens=900,
        response_schema=DispatcherDecision.model_json_schema(),
        metadata={"team_role": "dispatcher", "task_type": "dispatcher", "reasoning_effort": "low"},
    )
    seen: list[str] = []

    def observed(stage, response) -> None:
        seen.append(response.model)
        record_llm_cost(db, ctx.product.id, response, settings.llm_provider)

    try:
        reply = await asyncio.wait_for(llm.generate(request, on_observed=observed), timeout=DECISION_TIMEOUT_SECONDS)
        data = json.loads(reply.content)
        if isinstance(data, str):
            data = json.loads(data)
        return DispatcherDecision.model_validate(relax(DispatcherDecision, data)), (seen[-1] if seen else reply.model)
    except (asyncio.TimeoutError, ValueError, ValidationError, TypeError):
        _logger.warning("dispatcher_decision_failed", exc_info=True)
        return None, (seen[-1] if seen else None)
    except Exception:  # A provider outage must never break the page; the owner still gets chips and links.
        _logger.warning("dispatcher_provider_failed", exc_info=True)
        return None, (seen[-1] if seen else None)


# ---- acting -----------------------------------------------------------------------------

def _valid_chips(raw: list) -> list[dict]:
    """Chips the model suggested, kept only when they are complete, known and not blocked."""
    kept = []
    for item in raw:
        capability = CAPABILITIES.get(getattr(item, "capability", None))
        if capability is None or capability.id == "restricted_action":
            continue
        try:
            params = capability.params.model_validate(dict(item.params)).model_dump(exclude_none=True)
            kept.append(Chip(label=item.label, capability=capability.id,
                             params={k: str(v) for k, v in params.items()}).model_dump())
        except (ValidationError, ValueError):
            continue
    return kept


def _missing(error: ValidationError) -> str:
    names = {"topic": "тему", "channel": "площадку", "target": "раздел", "kind": "что именно"}
    fields = []
    for item in error.errors():
        name = str(item["loc"][0]) if item["loc"] else ""
        label = names.get(name, name)
        if label and label not in fields:
            fields.append(label)
    return "Уточните, пожалуйста: " + ", ".join(fields) + "." if fields else "Уточните, пожалуйста, детали."


def _record_tool_call(db: Session, ctx: Context, capability_id: str, params: dict, outcome: Outcome) -> None:
    db.add(ToolCall(
        product_id=ctx.product.id, task_id=outcome.task_id, tool_name="dispatcher", operation=capability_id,
        request_data={"params": params, "account": ctx.account},
        response_data={"ok": outcome.ok, "text": outcome.text[:300]},
        status="executed" if outcome.ok else "failed", executed_at=datetime.now(timezone.utc)))


def _confirm_block(confirmation: Confirmation, confirmation_id: str) -> dict:
    return {"type": "confirm", "confirmation_id": confirmation_id, "title": confirmation.title,
            "details": confirmation.details, "confirm_label": confirmation.confirm_label,
            "cancel_label": "Отмена", "paid": confirmation.paid, "state": "pending"}


async def act(ctx: Context, capability_id: str, raw_params: dict) -> tuple[Outcome, Confirmation | None, str]:
    """Run or prepare an ability under the policy. Returns (outcome, confirmation, parsed capability id)."""
    capability = CAPABILITIES[capability_id]
    try:
        params = capability.params.model_validate(raw_params)
    except ValidationError as error:
        return Outcome(_missing(error), ok=False), None, capability_id
    decision = verdict(capability.operation, capability.cost)
    if capability_id == "restricted_action":
        decision = Verdict.REFUSE
    if decision == Verdict.REFUSE:
        outcome = await executors.restricted_action(ctx, params) if capability_id == "restricted_action" else \
            Outcome("Это действие я сам не выполняю.", ok=False)
        return outcome, None, capability_id
    if decision == Verdict.CONFIRM_FIRST:
        described = await executors.DESCRIBERS[capability_id](ctx, params)
        if isinstance(described, Outcome):
            return described, None, capability_id
        return Outcome(f"{described.title} Подтвердите кнопкой.", described.title), described, capability_id
    outcome = await executors.EXECUTORS[capability_id](ctx, params)
    if capability.operation != ToolOperation.ANALYTICS:  # reads leave no ToolCall
        _record_tool_call(ctx.db, ctx, capability_id, params.model_dump(exclude_none=True), outcome)
    return outcome, outcome.confirmation, capability_id


FOLLOW_UP = [Chip(label="Что в работе", capability="show_work").model_dump()]


async def handle_message(db: Session, ctx: Context, llm: LLMService | None, *, text: str, client_key: str | None,
                         mode: str = "text", chip: dict | None = None) -> dict:
    """Process one owner message and return the user and assistant messages."""
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    # One message at a time per conversation; a retry with the same key returns the first answer.
    db.scalar(select(DispatcherConversation).where(DispatcherConversation.id == conversation.id).with_for_update())
    if client_key:
        existing = db.scalar(select(DispatcherMessage).where(
            DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.client_key == client_key))
        if existing is not None:
            reply = db.scalar(select(DispatcherMessage).where(
                DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.seq == existing.seq + 1))
            return {"messages": [to_dict(existing), *([to_dict(reply)] if reply else [])], "replayed": True}
    history = messages(db, conversation)
    label = (chip or {}).get("label") or text
    try:
        user = _add_committed(db, conversation, role="user", input_mode=mode, text=label, client_key=client_key,
                              capability=(chip or {}).get("capability"), params=(chip or {}).get("params") or {})
    except IntegrityError:
        return {"messages": [], "replayed": True}

    decision: DispatcherDecision | None = None
    model_name: str | None = None
    if chip is not None:
        known = CAPABILITIES.get(str(chip.get("capability")))
        if known is None or known.id == "restricted_action":
            decision = DispatcherDecision(intent="unsupported", confidence=1,
                                          reply_text="Такой кнопки я не знаю — выберите другой вариант.")
        else:
            decision = DispatcherDecision(intent="execute", capability=known.id,
                                          params={k: str(v) for k, v in (chip.get("params") or {}).items()},
                                          confidence=1.0)
    elif llm is not None:
        decision, model_name = await ask_model(db, llm, ctx.settings, ctx, history, text)

    outcome: Outcome | None = None
    confirmation: Confirmation | None = None
    capability_id: str | None = None
    intent = decision.intent if decision else "failed"
    reply_text, spoken, blocks = FALLBACK_TEXT, FALLBACK_TEXT, []
    chips = [chip_.model_dump() for chip_ in _fallback_chips()]
    if decision is not None:
        chips = _valid_chips(decision.next_chips)
        reply_text = decision.reply_text or reply_text
        spoken = decision.spoken or decision.reply_text or spoken
        if decision.intent == "execute":
            if decision.capability not in CAPABILITIES:
                intent = "unsupported"
                reply_text = "Этого я пока не умею. Я записал такую просьбу, чтобы добавить."
                spoken = reply_text
            elif decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = decision.reply_text or "Уточните, пожалуйста, что именно сделать?"
                spoken = reply_text
            else:
                outcome, confirmation, capability_id = await act(ctx, decision.capability, decision.params)
                reply_text, spoken = outcome.text, outcome.spoken or outcome.text
                blocks = outcome.blocks
                chips = [] if confirmation else FOLLOW_UP
        elif decision.intent == "unsupported":
            reply_text = decision.reply_text or "Этого я пока не умею."
    blocks = [{"type": "text", "text": reply_text}, *blocks]
    confirmation_id = None
    if confirmation is not None:
        confirmation_id = uuid4().hex
        blocks.append(_confirm_block(confirmation, confirmation_id))
    elif chips:
        blocks.append({"type": "chips", "items": chips})
    params = {**confirmation.params, "_capability": capability_id} if confirmation is not None else \
        (decision.params if decision else {})
    answer = _add_committed(db, conversation, role="assistant", input_mode="text", text=reply_text, spoken=spoken,
                            blocks=clean_blocks(blocks),
                            intent=intent, capability=capability_id or (decision.capability if decision else None),
                            params=params, confidence=(decision.confidence if decision else None),
                            task_id=outcome.task_id if outcome else None, model=model_name,
                            confirmation_id=confirmation_id, confirmation_state="pending" if confirmation else None)
    return {"messages": [to_dict(user), to_dict(answer)], "replayed": False}


def _fallback_chips() -> list[Chip]:
    return [Chip(label="Что в работе", capability="show_work"), Chip(label="Проверить готовое", capability="show_review"),
            Chip(label="Где что находится", capability="go_to", params={"target": "work"})]


# ---- confirmations ----------------------------------------------------------------------

async def confirm(db: Session, ctx: Context, confirmation_id: str, *, accept: bool, via: str = "button") -> dict:
    """Answer a confirmation card. A card works once; a voice 'yes' only answers the latest open card."""
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    card = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.confirmation_id == confirmation_id,
        DispatcherMessage.role == "assistant").with_for_update())
    if card is None:
        raise LookupError("Карточка не найдена")
    if card.confirmation_state != "pending":
        return {"messages": [to_dict(card)], "replayed": True}
    if via == "voice":
        latest = db.scalar(select(DispatcherMessage).where(
            DispatcherMessage.conversation_id == conversation.id,
            DispatcherMessage.confirmation_state == "pending").order_by(DispatcherMessage.seq.desc()).limit(1))
        if latest is None or latest.id != card.id:
            raise PermissionError("Голосом можно ответить только на последний вопрос")
    stored = dict(card.params or {})
    capability_id = str(stored.pop("_capability", card.capability or ""))
    card.confirmation_state = "confirmed" if accept else "declined"
    card.decided_at = datetime.now(timezone.utc)
    card.blocks = [({**b, "state": card.confirmation_state} if b.get("type") == "confirm" else b) for b in card.blocks]
    if accept:
        outcome = await executors.CONFIRMERS[capability_id](ctx, stored, via)
        _record_tool_call(db, ctx, capability_id, stored, outcome)
    else:
        outcome = await _decline(ctx, capability_id, stored)
    db.commit()  # The card's answer is final before the reply is numbered.
    reply = _add_committed(db, conversation, role="assistant", input_mode="confirm", text=outcome.text,
                           spoken=outcome.spoken,
                           blocks=clean_blocks([{"type": "text", "text": outcome.text}, *outcome.blocks,
                                                {"type": "chips", "items": FOLLOW_UP}]),
                           intent="answer", capability=capability_id, task_id=outcome.task_id)
    return {"messages": [to_dict(card), to_dict(reply)], "replayed": False}


async def _decline(ctx: Context, capability_id: str, stored: dict) -> Outcome:
    if capability_id == "whiteboard_video":
        from app.content.whiteboard.jobs import cancel_job
        try:
            cancel_job(ctx.db, ctx.product.id, UUID(str(stored["task_id"])))
        except (KeyError, ValueError, LookupError):
            ctx.db.rollback()
        return Outcome("Хорошо, видео не запускаю — задание отменено.", "Хорошо, видео не запускаю.")
    return Outcome("Хорошо, ничего не меняю.", "Хорошо, ничего не меняю.")
