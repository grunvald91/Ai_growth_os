"""One dispatcher turn: understand the request, check policy in code, act, answer, record.

A message either arrives as a hint chip (a complete request, no model call) or as free text
(the model returns a structured decision). The server never trusts the model for permission.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dispatcher import executors
from app.dispatcher.blocks import Chip, clean_blocks
from app.dispatcher.decision import (CONFIDENCE_TO_ACT, SYSTEM_PROMPT, DispatcherDecision, PlannedStep, build_user_message)
from app.dispatcher.executors import Confirmation, Context, Outcome
from app.dispatcher.fast_routes import quick_capability, quick_video_request, requested_story_count
from app.dispatcher.models import DispatcherConversation, DispatcherMessage
from app.dispatcher.policy import Cost, Verdict, verdict
from app.dispatcher.readers import snapshot
from app.dispatcher.registry import CAPABILITIES
from app.llm.costing import usage_cost_details
from app.llm.lenient import relax
from app.llm.schemas import LLMMessage, LLMRequest
from app.llm.service import LLMService
from app.models.domain import CostRecord, ToolCall
from app.tools import ToolOperation

_logger = logging.getLogger(__name__)

# A network-outage safeguard, NOT a response-speed strategy. Normal requests
# should finish promptly on the dedicated dispatcher model. Other generations
# retain independent longer time budgets.
DECISION_TIMEOUT_SECONDS = 45
# The model sees only the latest messages of the thread (owner's decision, 22.10.2026); the whole
# thread stays stored and visible on the page.
HISTORY_FOR_MODEL = 20

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
    response = {"id": str(message.id), "seq": message.seq, "role": message.role, "mode": message.input_mode,
                "text": message.text, "spoken": message.spoken, "blocks": message.blocks, "intent": message.intent,
                "created_at": message.created_at.isoformat() if message.created_at else None}
    # A task-result message can reconcile the original task card in a saved
    # conversation. Expose only the known status, never the other task params.
    status = (message.params or {}).get("status") if message.capability == "task_result" else None
    if message.task_id and status in {"completed", "failed"}:
        response["task_result"] = {"task_id": str(message.task_id), "status": status}
    return response


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

def record_llm_cost(db: Session, product_id: UUID, response, provider: str, *, latency_ms: int | None = None) -> None:
    usage = response.usage
    db.add(CostRecord(
        product_id=product_id, category="llm", provider=provider, amount=0, currency="USD",
        input_tokens=usage.input_tokens if usage is not None else None,
        output_tokens=usage.output_tokens if usage is not None else None,
        incurred_at=datetime.now(timezone.utc),
        details={**usage_cost_details(response), "task_type": "dispatcher", "model": response.model,
                 "usage_incomplete": usage is None,
                 "dispatcher_latency_ms": latency_ms},
    ))
    db.commit()


async def ask_model(db: Session, llm: LLMService, settings, ctx: Context, history: list[DispatcherMessage],
                    text: str) -> tuple[DispatcherDecision | None, str | None]:
    """(decision, model name). A model failure or unreadable answer gives (None, ...)."""
    turns = [LLMMessage(role=m.role, content=m.text) for m in history if m.text][-HISTORY_FOR_MODEL:]
    while turns and turns[0].role != "user":
        turns.pop(0)  # Start the shortened thread with the owner's message, as models expect.
    request = LLMRequest(
        system_prompt=SYSTEM_PROMPT,
        messages=[*turns, LLMMessage(role="user", content=build_user_message(snapshot(ctx.db, ctx.product), text))],
        model=(settings.dispatcher_llm_model if settings.llm_provider == "xai" else settings.llm_model),
        max_tokens=900,
        response_schema=DispatcherDecision.model_json_schema(),
        metadata={"team_role": "dispatcher", "task_type": "dispatcher", "reasoning_effort": "low"},
    )
    seen: list[str] = []
    started = time.monotonic()

    def observed(stage, response) -> None:
        seen.append(response.model)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        record_llm_cost(db, ctx.product.id, response, settings.llm_provider,
                        latency_ms=elapsed_ms)

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
    names = {"topic": "тему", "channel": "площадку", "target": "раздел", "kind": "что именно",
             "feedback": "что поправить (хотя бы несколько слов)", "when": "дату и время",
             "length": "длину видео: 30 секунд или минута", "days": "срок в днях (от 7 до 365)", "call_to_action": "призыв в конце (от 6 символов)"}
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


def _confirm_block(confirmation: Confirmation, confirmation_id: str, capability_id: str | None = None) -> dict:
    from app.dispatcher.editable import draft_view
    return {"type": "confirm", "confirmation_id": confirmation_id, "title": confirmation.title,
            "details": confirmation.details, "confirm_label": confirmation.confirm_label,
            "cancel_label": "Отмена", "paid": confirmation.paid, "state": "pending",
            "revision": 0, "editable": draft_view(capability_id, confirmation.params),
            "change_preview": confirmation.change_preview}


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
    if capability.always_confirm and decision == Verdict.RUN:
        decision = Verdict.CONFIRM_FIRST
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

# Explicit allowlist: even an ANALYTICS operation may mutate saved preferences.
# Do not infer permission from the model, the capability description or its cost.
READ_ONLY_PLAN_CAPABILITIES = frozenset({
    "show_work", "show_review", "show_plan", "show_recent_videos", "show_material", "go_to",
})


def _preflight_readonly_plan(steps: list[PlannedStep]) -> tuple[list[tuple[str, object]], str | None]:
    """Validate every step before executing even the first read.

    A plan never creates tasks, confirms publication, changes settings or calls
    a paid model. Invalid/mixed plans are refused in full, not partially run.
    """
    if not 2 <= len(steps) <= 3:
        return [], "В одном плане нужно от двух до трёх просмотров данных."
    parsed: list[tuple[str, object]] = []
    seen = set()
    for step in steps:
        cap = CAPABILITIES.get(step.capability)
        if (not cap or cap.id not in READ_ONLY_PLAN_CAPABILITIES
                or cap.operation != ToolOperation.ANALYTICS or cap.cost != Cost.NONE
                or cap.always_confirm):
            return [], ("В цепочке есть действие, требующее отдельного запуска или подтверждения. "
                        "Его нельзя выполнять автоматически вместе с просмотрами.")
        try:
            params = cap.params.model_validate(step.params)
        except ValidationError as exc:
            return [], _missing(exc)
        signature = (cap.id, json.dumps(params.model_dump(mode="json"), sort_keys=True))
        if signature in seen:
            return [], "В поручении повторяется одно и то же действие. Уточните, что сравнить."
        seen.add(signature)
        parsed.append((cap.id, params))
    return parsed, None


async def run_readonly_plan(ctx: Context, steps: list[PlannedStep]) -> Outcome:
    """Bounded serial execution. Stop on failure; no writes or hidden approvals."""
    parsed, error = _preflight_readonly_plan(steps)
    if error:
        return Outcome(error, ok=False)
    results: list[dict] = []
    failed = False
    completed = 0
    for capability_id, params in parsed:
        title = CAPABILITIES[capability_id].title
        if failed:
            results.append({"title": title, "status": "skipped",
                            "detail": "Не выполнялось: предыдущий шаг завершился ошибкой.", "blocks": []})
            continue
        try:
            result, confirmation, _ = await act(ctx, capability_id, params.model_dump(exclude_none=True))
            # Defense in depth: never render a hidden confirmation as a completed step.
            if confirmation is not None:
                result = Outcome("Действие требует отдельного подтверждения.", ok=False)
        except Exception:
            _logger.exception("dispatcher_readonly_plan_step_failed", extra={"capability": capability_id})
            result = Outcome("Не удалось получить данные этого шага.", ok=False)
        if result.ok:
            completed += 1
        else:
            failed = True
        # Typed blocks: links and tables still come from existing scoped
        # executors, not model-authored content; confirmations stay outside.
        child_blocks = [
            block for block in clean_blocks(result.blocks)
            if block["type"] in {"text", "chips", "links", "table",
                                 "resource_list", "task", "material"}
        ][:8]
        results.append({
            "title": title,
            "status": ("failed" if not result.ok else "no_data" if result.no_data else "completed"),
            "detail": result.text[:600],
            "blocks": child_blocks,
        })
    summary = (f"Проверено {completed} из {len(parsed)} разделов."
               if not failed else
               f"Проверено {completed} из {len(parsed)} разделов. Остальные остановлены из-за ошибки.")
    return Outcome(summary, summary, [{"type": "workflow", "title": "Результаты поручения",
                                       "steps": results}], ok=not failed)





def _apply_create_hints(decision: DispatcherDecision, hints, text: str) -> None:
    """Merge optional UI settings only after dispatcher has determined an action.

    Explicitly spoken screen count and channel take precedence over older UI
    settings. Hints alone never start work.
    """
    if hints is None or decision.intent != "execute":
        return
    capability = decision.capability
    if capability not in {"create_post", "create_creative", "create_package"}:
        return
    params = decision.params
    target = ("post" if capability == "create_post" else
              "package" if capability == "create_package" else params.get("format"))
    if hints.audience and not params.get("audience"):
        params["audience"] = hints.audience
    if hints.editorial_brief and not params.get("editorial_brief"):
        params["editorial_brief"] = hints.editorial_brief
    if target in {"story", "static_image"} and hints.format == target:
        if hints.image_model:
            params["image_model"] = hints.image_model
        if hints.aspect_ratio:
            params["aspect_ratio"] = hints.aspect_ratio
    if capability == "create_creative" and target == "story":
        spoken_count = requested_story_count(text)
        if spoken_count is not None:
            params["frame_count"] = spoken_count
        elif hints.format == "story" and hints.frame_count is not None:
            params["frame_count"] = hints.frame_count
    if hints.format != target or not hints.platform:
        return
    # Explicitly spoken platforms override the manual dropdown.
    explicit_channel = re.search(
        r"(?iu)(?:телеграм|telegram|вконтакте|\bvk\b|инстаграм|instagram|youtube|ютуб|дзен|dzen|tenchat|vc\.ru)",
        text)
    if explicit_channel:
        return
    if capability == "create_post":
        params["channel"] = hints.platform
    elif capability == "create_creative":
        params["platform"] = hints.platform


def _visual_preferences(text: str) -> tuple[str | None, str | None]:
    """Explicit user constraints outrank the provider's or model's defaults."""
    value = (text or "").casefold().replace("ё", "е")
    candidates = (
        ("instagram_reels", r"(?:instagram|инстаграм\w*)"),
        ("vk", r"(?:вконтакте|\bвк\b|\bvk\b)"),
        ("telegram", r"(?:telegram|телеграм\w*)"),
        ("dzen", r"(?:\bдзен\b|\bdzen\b)"),
        ("vc_ru", r"\bvc\.ru\b"),
        ("tenchat", r"\btenchat\b"),
    )
    channel = next((name for name, expression in candidates
                    if re.search(expression, value, re.I)), None)
    ratio = next((x for x in ("9:16", "4:5", "1:1", "16:9") if x in value), None)
    if ratio is None:
        if re.search(r"горизонтальн\w*", value):
            ratio = "16:9"
        elif re.search(r"(?:вертикальн\w*|портретн\w*)", value):
            ratio = "4:5"
        elif re.search(r"квадрат\w*", value):
            ratio = "1:1"
    return channel, ratio


async def handle_message(db: Session, ctx: Context, llm: LLMService | None, *, text: str, client_key: str | None,
                         mode: str = "text", chip: dict | None = None, creation=None) -> dict:
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
    else:
        quick = quick_capability(text)
        if quick is not None:
            decision = DispatcherDecision(intent="execute", capability=quick, params={}, confidence=1.0)
        elif (video_params := quick_video_request(text)) is not None:
            # Exact topic and duration from the user's explicit request. Existing
            # whiteboard executor still requires paid approval before rendering.
            decision = DispatcherDecision(intent="execute", capability="whiteboard_video",
                                          params=video_params, confidence=1.0)
        elif llm is not None:
            decision, model_name = await ask_model(db, llm, ctx.settings, ctx, history, text)


    if decision is not None:
        # A request for an illustrated post must produce an image AND ready
        # caption, never silently degrade into text-only create_post.
        if (chip is None and decision.intent == "execute"
                and decision.capability in {"create_post", "create_creative"}
                and re.search(r"(?iu)\b(?:пост\w*|публикаци\w*)\b", text)
                and re.search(r"(?iu)(?:картин\w*|изображени\w*|иллюстраци\w*|фото|обложк\w*)", text)
                and not re.search(r"(?iu)без\s+(?:картин\w*|изображени\w*|фото)", text)):
            old = decision.params
            spoken_platform, spoken_ratio = _visual_preferences(text)
            default_platform = spoken_platform or old.get("channel") or old.get("platform") or "telegram"
            decision.capability = "create_creative"
            decision.params = {
                "topic": old.get("topic") or text.strip()[:300],
                "format": "static_image",
                "platform": default_platform,
                "image_model": old.get("image_model") or "auto",
                **({"aspect_ratio": spoken_ratio} if spoken_ratio else {}),
                "editorial_brief": (
                    (old.get("editorial_brief") or "") +
                    "\nПодготовь полноценный текст поста в поле caption вместе с готовым изображением."
                ).strip()[:3500],
                **({"audience": old["audience"]} if old.get("audience") else {}),
            }
        # For a naturally described image, accept explicit channel/orientation
        # even when the LLM fails to preserve these details.
        if (decision.intent == "execute" and decision.capability == "create_creative"
                and decision.params.get("format") == "static_image"):
            spoken_platform, spoken_ratio = _visual_preferences(text)
            if spoken_platform:
                decision.params["platform"] = spoken_platform
            if spoken_ratio:
                decision.params["aspect_ratio"] = spoken_ratio
        _apply_create_hints(decision, creation, text)
        # Explicit voice/text counts are authoritative without any manual selection.
        if (creation is None and decision.intent == "execute"
                and decision.capability == "create_creative"
                and decision.params.get("format") == "story"):
            count = requested_story_count(text)
            if count is not None:
                decision.params["frame_count"] = count
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
        elif decision.intent == "plan":
            if decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = decision.reply_text or "Уточните, пожалуйста, какие данные проверить."
                spoken = reply_text
            else:
                outcome = await run_readonly_plan(ctx, decision.steps)
                reply_text, spoken = outcome.text, outcome.spoken or outcome.text
                blocks = outcome.blocks
                if not outcome.ok:
                    intent = "clarify" if not blocks else "plan"
                chips = FOLLOW_UP if blocks else []
        elif decision.intent == "unsupported":
            reply_text = decision.reply_text or "Этого я пока не умею."
    blocks = [{"type": "text", "text": reply_text}, *blocks]
    confirmation_id = None
    if confirmation is not None:
        confirmation_id = uuid4().hex
        blocks.append(_confirm_block(confirmation, confirmation_id, capability_id))
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

async def edit_confirmation(db: Session, ctx: Context, edit) -> dict:
    """Edit a content request under the same conversation lock as confirmation.

    Never queues work: only the explicit /confirm endpoint may start the saved draft.
    """
    from app.dispatcher.editable import EDITABLE_CAPABILITIES, target_for_edit, draft_view
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    card = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id,
        DispatcherMessage.confirmation_id == edit.confirmation_id,
        DispatcherMessage.role == "assistant").with_for_update())
    if card is None:
        raise LookupError("Карточка не найдена")
    if card.confirmation_state != "pending":
        raise PermissionError("Задание уже подтверждено или отменено")
    old_capability = (card.params or {}).get("_capability", card.capability)
    if old_capability not in EDITABLE_CAPABILITIES:
        raise PermissionError("Это подтверждение нельзя редактировать")
    blocks = list(card.blocks or [])
    current = next((b for b in blocks if b.get("type") == "confirm"
                    and b.get("confirmation_id") == edit.confirmation_id), None)
    if current is None or current.get("state") != "pending":
        raise PermissionError("Карточка уже недоступна")
    revision = int(current.get("revision", 0))
    if edit.revision != revision:
        raise PermissionError("Карточка была изменена. Откройте обновлённую версию")
    capability_id, params = target_for_edit(edit, card.params)
    described = await executors.DESCRIBERS[capability_id](ctx, params)
    if not isinstance(described, Confirmation):
        raise ValueError("Не удалось подготовить подтверждение")
    replacement = _confirm_block(described, edit.confirmation_id, capability_id)
    replacement["revision"] = revision + 1
    card.blocks = clean_blocks([
        *[{"type": "text", "text": "Параметры обновлены. Проверьте и подтвердите создание."}],
        *[replacement if b.get("type") == "confirm" else b
          for b in blocks if b.get("type") != "text"],
    ])
    card.params = {**described.params, "_capability": capability_id}
    card.capability = capability_id
    card.text = "Параметры обновлены. Проверьте и подтвердите создание."
    card.spoken = card.text
    db.commit()
    return {"messages": [to_dict(card)], "replayed": False}



async def confirm(db: Session, ctx: Context, confirmation_id: str, *, accept: bool,
                  via: str = "button", revision: int | None = None) -> dict:
    """Answer a confirmation card. A card works once; a voice 'yes' only answers the latest open card."""
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    card = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.confirmation_id == confirmation_id,
        DispatcherMessage.role == "assistant").with_for_update())
    if card is None:
        raise LookupError("Карточка не найдена")
    if card.confirmation_state != "pending":
        return {"messages": [to_dict(card)], "replayed": True}
    current_block = next((b for b in (card.blocks or [])
                          if b.get("type") == "confirm" and b.get("confirmation_id") == confirmation_id), None)
    if current_block and current_block.get("editable"):
        saved_revision = int(current_block.get("revision", 0))
        # Legacy clients can approve unedited cards, but a modified card
        # must carry its current revision. A stale phone/tab cannot approve
        # different material than what its user actually reviewed.
        if (revision is None and saved_revision != 0) or (
                revision is not None and revision != saved_revision):
            raise PermissionError("Задание изменилось. Откройте обновлённую карточку перед созданием")
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
