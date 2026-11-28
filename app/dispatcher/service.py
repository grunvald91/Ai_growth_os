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
from dataclasses import replace
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
from app.models.domain import CostRecord, Product, ToolCall
from app.tools import ToolOperation

_logger = logging.getLogger(__name__)

# A network-outage safeguard, NOT a response-speed strategy. Normal requests
# should finish promptly on the dedicated dispatcher model. Other generations
# retain independent longer time budgets.
DECISION_TIMEOUT_SECONDS = 45
# The model sees only the latest messages of the thread (owner's decision, 22.10.2026); the whole
# thread stays stored and visible on the page.
HISTORY_FOR_MODEL = 20

FALLBACK_TEXT = ("Не удалось получить рабочий ответ ИИ-диспетчера. Это технический сбой, а не ошибка в вашей просьбе. Повторите запрос позже или воспользуйтесь доступными действиями.")


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
    # The reserved key is assigned only by the server on preparation-form submission.
    # Expose only the source message ID, not the submitted private parameters.
    if message.role == "user" and (message.client_key or "").startswith("form:"):
        response["submitted_form_id"] = message.client_key.removeprefix("form:")
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

def pending_creation_cards(history: list[DispatcherMessage]) -> list[dict]:
    """Only server-persisted, pending editable cards from this conversation."""
    from app.dispatcher.editable import EDITABLE_CAPABILITIES
    fields = ("format", "topic", "platform", "frame_count", "audience",
              "editorial_brief", "image_model", "aspect_ratio")
    result = []
    for message in reversed(history):
        if (message.role != "assistant" or message.confirmation_state != "pending"
                or (message.params or {}).get("_capability", message.capability) not in EDITABLE_CAPABILITIES):
            continue
        for block in message.blocks or []:
            if (block.get("type") == "confirm" and block.get("state") == "pending"
                    and block.get("confirmation_id") == message.confirmation_id
                    and isinstance(block.get("editable"), dict)):
                result.append({"confirmation_id": message.confirmation_id,
                               "revision": block.get("revision", 0),
                               **{key: block["editable"].get(key) for key in fields}})
                break
        if len(result) == 4:
            break
    return result


def pending_video_forms(history: list[DispatcherMessage]) -> list[dict]:
    from app.dispatcher.forms import VIDEO_EDIT_FIELDS
    consumed = {m.client_key for m in history if m.role == "user"}
    result = []
    for message in reversed(history):
        if (message.role != "assistant" or message.intent != "form"
                or message.capability != "whiteboard_video"
                or "form:" + str(message.id) in consumed):
            continue
        for block in message.blocks or []:
            if block.get("type") == "tool_form" and block.get("capability") == "whiteboard_video":
                result.append({"message_id": str(message.id), "revision": block.get("revision", 0),
                               **{f["name"]: f["value"] for f in block["fields"]
                                  if f["name"] in VIDEO_EDIT_FIELDS}})
                break
        if len(result) == 2:
            break
    return result


def pending_read_forms(history: list[DispatcherMessage]) -> list[dict]:
    """Reusable read forms; only previously persisted controls are editable."""
    from app.dispatcher.forms import READ_FORMS
    result = []
    for message in reversed(history):
        if (message.role != "assistant" or message.intent != "form"
                or message.capability not in READ_FORMS):
            continue
        for block in message.blocks or []:
            if block.get("type") == "tool_form" and block.get("capability") == message.capability:
                result.append({"message_id": str(message.id), "revision": block.get("revision", 0),
                               "capability": message.capability,
                               **{f["name"]: f["value"] for f in block["fields"]}})
                break
        if len(result) == 2:
            break
    return result


def pending_prepare_forms(history: list[DispatcherMessage]) -> list[dict]:
    """Unsubmitted creation forms. Format is context, never an editable control."""
    from app.dispatcher.forms import PREPARE_FORMS
    consumed = {m.client_key for m in history if m.role == "user"}
    result = []
    for message in reversed(history):
        if (message.role != "assistant" or message.intent != "form"
                or message.capability not in PREPARE_FORMS - {"whiteboard_video"}
                or "form:" + str(message.id) in consumed):
            continue
        for block in message.blocks or []:
            if block.get("type") == "tool_form" and block.get("capability") == message.capability:
                result.append({"message_id": str(message.id), "revision": block.get("revision", 0),
                               "capability": message.capability,
                               **{f["name"]: f["value"] for f in block["fields"]}})
                break
        if len(result) == 2:
            break
    return result


def edit_saved_form(db, conversation, decision, snapshot):
    from app.dispatcher.forms import PREPARE_FORMS, READ_FORMS, VIDEO_EDIT_FIELDS, form_for
    raw = dict(decision.params)
    target = raw.pop("message_id", None)
    revision = raw.pop("revision", None)
    candidate = next((item for item in snapshot if item["message_id"] == target), None)
    if candidate is None or revision != str(candidate["revision"]) or not raw:
        raise ValueError("Уточните параметры открытой формы.")
    capability = candidate.get("capability", "whiteboard_video")
    if capability == "whiteboard_video":
        allowed = VIDEO_EDIT_FIELDS
    elif capability in READ_FORMS or capability in PREPARE_FORMS:
        allowed = set(candidate) - {"message_id", "revision", "capability", "format"}
    else:
        raise ValueError("Форма недоступна.")
    if not raw.keys() <= allowed:
        raise ValueError("Недопустимый параметр формы.")
    source = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.id == UUID(target), DispatcherMessage.conversation_id == conversation.id,
        DispatcherMessage.role == "assistant", DispatcherMessage.intent == "form",
        DispatcherMessage.capability == capability).with_for_update().execution_options(populate_existing=True))
    if source is None:
        raise LookupError("Форма не найдена.")
    if db.scalar(select(DispatcherMessage.id).where(
            DispatcherMessage.conversation_id == conversation.id,
            DispatcherMessage.client_key == "form:" + target)):
        raise PermissionError("Форма уже отправлена.")
    old = next((b for b in source.blocks or [] if b.get("type") == "tool_form"), None)
    if (old is None or old.get("capability") != capability
            or old.get("revision", 0) != candidate["revision"]):
        raise PermissionError("Форма изменилась.")
    if not raw.keys() <= {f["name"] for f in old["fields"]}:
        raise ValueError("Недопустимый параметр формы.")
    values = {f["name"]: f["value"] for f in old["fields"] if f["value"] != ""}
    for key, value in raw.items():
        if not value and ((capability == "whiteboard_video" and key == "call_to_action")
                          or (capability == "show_insights" and key == "days")
                          or (capability in PREPARE_FORMS - {"whiteboard_video"}
                              and key in {"topic", "audience", "editorial_brief", "platform",
                                          "frame_count", "aspect_ratio", "image_ratio", "image_model"})):
            values.pop(key, None)
        else:
            values[key] = value
    # Changing filters starts a fresh result set unless the owner explicitly
    # supplied a different page in this same edit.
    if capability in {"list_tasks", "list_publications"} and "page" not in raw:
        if raw.keys() & {"tab", "kind", "query", "channel", "status"}:
            values["page"] = "1"
    updated = form_for(capability, values)
    updated["revision"] = candidate["revision"] + 1
    source.blocks = clean_blocks([updated if b.get("type") == "tool_form" else b for b in source.blocks])
    db.flush()
    return to_dict(source)


def card_edit_request(decision: DispatcherDecision, pending: list[dict]):
    """Pin model proposals to the exact snapshot; the locked editor rechecks revision."""
    from app.dispatcher.editable import EditCreation
    allowed = {"topic", "platform", "frame_count", "audience",
               "editorial_brief", "image_model", "aspect_ratio"}
    raw = dict(decision.params)
    target = raw.pop("confirmation_id", None)
    revision = raw.pop("revision", None)
    card = next((item for item in pending if item["confirmation_id"] == target), None)
    if card is None or revision != str(card["revision"]) or not raw or not raw.keys() <= allowed:
        raise ValueError("Уточните, какую открытую карточку и какие параметры изменить.")
    merged = {**card, **raw}
    return EditCreation.model_validate(merged)


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
                    text: str, *, recovery: bool = False) -> tuple[DispatcherDecision | None, str | None]:
    """(decision, model name). A model failure or unreadable answer gives (None, ...)."""
    turns = [LLMMessage(role=m.role, content=m.text) for m in history if m.text][-HISTORY_FOR_MODEL:]
    while turns and turns[0].role != "user":
        turns.pop(0)  # Start the shortened thread with the owner's message, as models expect.
    request = LLMRequest(
        system_prompt=SYSTEM_PROMPT + ("\nПовторная проверка: первый ответ не нашёл действие или назвал неизвестное действие. "
            "Заново сопоставь исходную цель со всем каталогом. Выбери точное действие с реальными параметрами, "
            "либо предложи ближайшее доступное действие с пояснением, либо задай один предметный вопрос. "
            "Не повторяй отказ автоматически. Не выдумывай возможностей и не обходи подтверждения." if recovery else ""),
        messages=[*turns, LLMMessage(role="user", content=build_user_message({**snapshot(ctx.db, ctx.product),
                "pending_creation_cards": pending_creation_cards(history),
                "pending_video_forms": pending_video_forms(history),
                "pending_read_forms": pending_read_forms(history),
                "pending_prepare_forms": pending_prepare_forms(history)}, text))],
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
        reply = await asyncio.wait_for(llm.generate(request, on_observed=observed), timeout=12 if recovery else DECISION_TIMEOUT_SECONDS)
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
    names = {"task_id": "какое именно задание", "material_id": "какой именно материал", "publication_id": "какую именно публикацию", "post_id": "какой именно пост",
             "extra_calls": "число дополнительных обращений (от 0 до 10)",
             "topic": "тему", "channel": "площадку", "target": "раздел", "kind": "что именно",
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
        _record_tool_call(ctx.db, ctx, capability_id, params.model_dump(mode="json", exclude_none=True), outcome)
    return outcome, outcome.confirmation, capability_id


class FormPreparationError(ValueError):
    """An owner-facing failure from a validated preparation capability."""


async def _prepare_form(ctx: Context, capability_id: str, params: dict):
    # The caller has flushed the submission and owns the transaction. Existing
    # executors (notably whiteboard) may commit/rollback their own work. Bind
    # those commits to savepoints until the reply and draft can commit together.
    with Session(bind=ctx.db.connection(), join_transaction_mode="create_savepoint") as work:
        scoped = replace(ctx, db=work, product=work.get(Product, ctx.product.id))
        outcome, confirmation, capability = await act(scoped, capability_id, params)
        if not outcome.ok:
            raise FormPreparationError(outcome.text)
        work.commit()
        return outcome, confirmation, capability


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





def _apply_create_hints(decision: DispatcherDecision, hints, text: str, *,
                        subject_only: bool = False) -> None:
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
        spoken_count = None if subject_only else requested_story_count(text)
        if spoken_count is not None:
            params["frame_count"] = spoken_count
        elif hints.format == "story" and hints.frame_count is not None:
            params["frame_count"] = hints.frame_count
    if hints.format != target or not hints.platform:
        return
    # Explicitly spoken platforms override the manual dropdown.
    explicit_channel = None if subject_only else re.search(
        r"(?iu)(?:телеграм|telegram|вконтакте|\bvk\b|инстаграм|instagram|youtube|ютуб|дзен|dzen|tenchat|vc\.ru)",
        text)
    if target == "post" and hints.format == "post":
        effective_channel = (params.get("channel") if explicit_channel
                             else hints.platform or params.get("channel"))
        if effective_channel == "vk":
            params["image_model"] = hints.image_model or "none"
            params["image_ratio"] = hints.aspect_ratio or "4:5"
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
                         mode: str = "text", chip: dict | None = None, creation=None, form_submission=None) -> dict:
    """Process one owner message and return the user and assistant messages."""
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    # One message at a time per conversation; a retry with the same key returns the first answer.
    db.scalar(select(DispatcherConversation).where(DispatcherConversation.id == conversation.id).with_for_update())
    preparing = False
    if client_key and client_key.startswith("form:"):
        raise ValueError("Ключ зарезервирован для отправки формы.")
    if form_submission is not None:
        from app.dispatcher.forms import PREPARE_FORMS, form_for
        source = db.scalar(select(DispatcherMessage).where(
            DispatcherMessage.id == form_submission.message_id,
            DispatcherMessage.conversation_id == conversation.id,
            DispatcherMessage.product_id == ctx.product.id,
            DispatcherMessage.role == "assistant",
            DispatcherMessage.intent == "form").with_for_update().execution_options(populate_existing=True))
        if source is None:
            raise LookupError("Форма не найдена в текущей беседе.")
        stored_form = next((b for b in source.blocks or [] if b.get("type") == "tool_form"), None)
        if stored_form is None or stored_form.get("capability") != source.capability:
            raise ValueError("Форма больше недоступна.")
        target = stored_form["capability"]
        if form_submission.revision != stored_form.get("revision", 0):
            raise ValueError("Форма изменилась. Откройте актуальную версию.")
        submitted = form_submission.params
        if not submitted.keys() <= {f["name"] for f in stored_form["fields"]}:
            raise ValueError("В форме есть неизвестные поля.")
        if target == "create_creative":
            saved_format = next((f["value"] for f in stored_form["fields"] if f["name"] == "format"), None)
            if not saved_format or submitted.get("format") != saved_format:
                raise ValueError("Формат изменился. Откройте отдельную форму.")
        # Re-check the current registry and validate the complete request.
        form_for(target, submitted, partial=False)
        preparing = target in PREPARE_FORMS
        if preparing:
            # One preparation per saved form, including cross-tab repeats.
            client_key = "form:" + str(source.id)
        chip = {"label": stored_form["title"], "capability": target, "params": submitted}
        mode = "chip"
    if client_key:
        existing = db.scalar(select(DispatcherMessage).where(
            DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.client_key == client_key))
        if existing is not None:
            if preparing:
                previous = {key: value for key, value in (existing.params or {}).items() if key != "_linked_reply"}
                if previous != submitted:
                    raise ValueError("Форма уже отправлена с другими параметрами. Измените карточку подтверждения.")
            reply = db.scalar(select(DispatcherMessage).where(
                DispatcherMessage.conversation_id == conversation.id,
                DispatcherMessage.role == "assistant",
                DispatcherMessage.params["_reply_to"].as_string() == str(existing.id)).limit(1))
            if reply is None and not (existing.params or {}).get("_linked_reply"):
                # Compatibility for older messages. Never replay an unrelated
                # update or a response explicitly linked to another request.
                candidate = db.scalar(select(DispatcherMessage).where(
                    DispatcherMessage.conversation_id == conversation.id,
                    DispatcherMessage.seq == existing.seq + 1,
                    DispatcherMessage.role == "assistant"))
                if (candidate is not None and candidate.capability != "task_result"
                        and not (candidate.params or {}).get("_reply_to")):
                    reply = candidate
            edited = None
            if reply is not None and (reply.params or {}).get("_edited_card_id"):
                edited = db.scalar(select(DispatcherMessage).where(
                    DispatcherMessage.conversation_id == conversation.id,
                    DispatcherMessage.id == UUID(reply.params["_edited_card_id"])))
            return {"messages": [to_dict(existing), *([to_dict(edited)] if edited else []),
                                 *([to_dict(reply)] if reply else [])], "replayed": True}
    history = messages(db, conversation)
    edit_snapshot = pending_creation_cards(history)
    form_snapshot = pending_video_forms(history) + pending_read_forms(history) + pending_prepare_forms(history)
    label = (chip or {}).get("label") or text
    write_message = _add if preparing else _add_committed
    try:
        user = write_message(db, conversation, role="user", input_mode=mode, text=label, client_key=client_key,
                              capability=(chip or {}).get("capability"),
                              params={**((chip or {}).get("params") or {}), "_linked_reply": True})
    except IntegrityError:
        db.rollback()
        return {"messages": [], "replayed": True}

    selected_topic_request = (
        chip is None and creation is not None
        and creation.topic is not None and creation.format is not None
        and text.strip() == creation.topic
    )
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
    elif selected_topic_request:
        # An explicit Create-format selection plus a topic is already a
        # complete user request. Do not ask the LLM to rediscover the chosen
        # capability: it can incorrectly say that post creation is unsupported.
        # act() still runs Pydantic validation and mandatory confirmation.
        topic_params = {"topic": creation.topic}
        if creation.format == "post":
            selected_capability = "create_post"
            topic_params["channel"] = creation.platform or "telegram"
        elif creation.format == "package":
            selected_capability = "create_package"
        elif creation.format == "whiteboard":
            # This existing executor creates only a WAITING_APPROVAL draft.
            # Paid video rendering still needs the owner's explicit confirmation.
            selected_capability = "whiteboard_video"
        else:
            selected_capability = "create_creative"
            topic_params["format"] = creation.format
            if creation.platform:
                topic_params["platform"] = creation.platform
        decision = DispatcherDecision(
            intent="execute", capability=selected_capability,
            params=topic_params, confidence=1.0)
        if creation.format == "whiteboard" and creation.video is not None:
            # Already validated owner form values, not model-generated strings.
            decision.params.update(creation.video.model_dump(exclude_none=True))
    else:
        # Explicit references to an existing form/card must reach the semantic
        # editor, not a shortcut matching words such as "готовые видео".
        refers_to_card = bool((form_snapshot or edit_snapshot) and re.search(
            r"(?iu)\b(?:форм(?:а|у|е|ы|ой|ах)|карточк(?:а|у|е|и|ой|ах)|фильтр\w*)\b", text))
        if not refers_to_card and (video_params := quick_video_request(text)) is not None:
            # Exact topic and duration from the user's explicit request. Existing
            # whiteboard executor still requires paid approval before rendering.
            decision = DispatcherDecision(intent="execute", capability="whiteboard_video",
                                          params=video_params, confidence=1.0)
        elif not refers_to_card and (quick := quick_capability(text)) is not None:
            decision = DispatcherDecision(intent="execute", capability=quick, params={}, confidence=1.0)
        elif llm is not None:
            decision, model_name = await ask_model(db, llm, ctx.settings, ctx, history, text)
            if decision is not None and (decision.intent == "unsupported" or (
                    decision.intent == "execute" and decision.capability not in CAPABILITIES)):
                # No tools have run yet. One bounded semantic recovery pass, not a second execution.
                recovered, recovered_model = await ask_model(
                    db, llm, ctx.settings, ctx, history, text, recovery=True)
                if recovered is not None and recovered.intent != "unsupported" and (
                        recovered.intent != "execute" or recovered.capability in CAPABILITIES):
                    decision, model_name = recovered, recovered_model or model_name


    if decision is not None:
        # A request for an illustrated post must produce an image AND ready
        # caption, never silently degrade into text-only create_post.
        if (chip is None and not selected_topic_request and decision.intent == "execute"
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
        if (not selected_topic_request and decision.intent == "execute"
                and decision.capability == "create_creative"
                and decision.params.get("format") == "static_image"):
            spoken_platform, spoken_ratio = _visual_preferences(text)
            if spoken_platform:
                decision.params["platform"] = spoken_platform
            if spoken_ratio:
                decision.params["aspect_ratio"] = spoken_ratio
        _apply_create_hints(decision, creation, text, subject_only=selected_topic_request)
        # Explicit voice/text counts are authoritative without any manual selection.
        if (creation is None and decision.intent == "execute"
                and decision.capability == "create_creative"
                and decision.params.get("format") == "story"):
            count = requested_story_count(text)
            if count is not None:
                decision.params["frame_count"] = count
    outcome: Outcome | None = None
    workflow_id: str | None = None
    confirmation: Confirmation | None = None
    capability_id: str | None = None
    intent = decision.intent if decision else "failed"
    reply_text, spoken, blocks = FALLBACK_TEXT, FALLBACK_TEXT, []
    chips = [chip_.model_dump() for chip_ in _fallback_chips()]
    if decision is not None:
        chips = _valid_chips(decision.next_chips)
        reply_text = decision.reply_text or reply_text
        spoken = decision.spoken or decision.reply_text or spoken
        if decision.intent == "form":
            from app.dispatcher.forms import form_for
            chips = []
            if decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = spoken = "Что именно вы хотите посмотреть?"
            else:
                try:
                    blocks = [form_for(decision.capability, decision.params)]
                except ValueError:
                    intent = "clarify"
                    reply_text = spoken = "Уточните параметры: выбранные значения должны подходить для этого действия."
                else:
                    reply_text = spoken = ("Укажите параметры и нажмите «" + blocks[0]["submit_label"] + "».")
        elif decision.intent == "edit_form":
            chips = []
            if decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = spoken = "Какой параметр открытой формы изменить?"
            else:
                try:
                    edited = edit_saved_form(db, conversation, decision, form_snapshot)
                    answer = _add(db, conversation, role="assistant", input_mode="text",
                                  text="Параметры формы обновлены. Проверьте их перед отправкой.",
                                  spoken="Параметры формы обновлены.",
                                  blocks=[{"type": "text", "text": "Параметры формы обновлены. Проверьте их перед отправкой."}],
                                  intent="edit_form", params={"_edited_card_id": edited["id"], "_reply_to": str(user.id)},
                                  confidence=decision.confidence, model=model_name)
                    db.commit()
                    return {"messages": [to_dict(user), edited, to_dict(answer)], "replayed": False}
                except (LookupError, PermissionError, ValueError):
                    db.rollback()
                    intent = "clarify"
                    reply_text = spoken = "Форма недоступна или изменилась. Уточните, что нужно изменить."
        elif decision.intent == "edit_card":
            chips = []
            if decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = spoken = "Уточните, какой параметр открытой карточки изменить."
            else:
                try:
                    edit = card_edit_request(decision, edit_snapshot)
                    updated = await edit_confirmation(db, ctx, edit, commit=False)
                    edited = updated["messages"][0]
                    # Same transaction for the revised card and acknowledgement.
                    # Replaying the message returns the current saved card, not a second edit.
                    answer = _add(db, conversation, role="assistant", input_mode="text",
                                  text="Параметры обновлены. Проверьте карточку перед подтверждением.",
                                  spoken="Параметры обновлены.",
                                  blocks=[{"type": "text", "text": "Параметры обновлены. Проверьте карточку перед подтверждением."}],
                                  intent="edit_card", params={"_edited_card_id": edited["id"], "_reply_to": str(user.id)},
                                  confidence=decision.confidence, model=model_name)
                    db.commit()
                    return {"messages": [to_dict(user), edited, to_dict(answer)], "replayed": False}
                except (LookupError, PermissionError, ValueError) as exc:
                    db.rollback()
                    intent = "clarify"
                    reply_text = spoken = ("Не удалось изменить карточку. Проверьте её актуальные параметры "
                                           "и уточните, что нужно изменить.")
        elif decision.intent == "execute":
            if decision.capability not in CAPABILITIES:
                intent = "clarify"
                reply_text = "Не удалось подобрать точное действие. Какой результат вы хотите получить?"
                spoken = reply_text
                chips = chips or [item.model_dump() for item in _fallback_chips()]
            elif decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = decision.reply_text or "Уточните, пожалуйста, что именно сделать?"
                spoken = reply_text
            else:
                if preparing:
                    try:
                        outcome, confirmation, capability_id = await _prepare_form(ctx, decision.capability, decision.params)
                    except FormPreparationError:
                        db.rollback()
                        raise
                else:
                    outcome, confirmation, capability_id = await act(ctx, decision.capability, decision.params)
                reply_text, spoken = outcome.text, outcome.spoken or outcome.text
                blocks = outcome.blocks
                # Keep validated alternatives when the chosen action cannot run.
                # They are opt-in chips, not additional executed operations.
                chips = [] if confirmation else (chips or FOLLOW_UP)
        elif decision.intent == "plan":
            if decision.confidence < CONFIDENCE_TO_ACT:
                intent = "clarify"
                reply_text = decision.reply_text or "Уточните, пожалуйста, какие данные проверить."
                spoken = reply_text
            elif any(step.capability not in READ_ONLY_PLAN_CAPABILITIES for step in decision.steps):
                # Persist mixed workflows; mutating steps *never* execute here.
                # Every draft-post step requires its own explicit approval.
                from app.dispatcher import workflows
                try:
                    workflow = await workflows.create(ctx, conversation, decision.steps, client_key)
                except (ValueError, PermissionError) as exc:
                    intent = "clarify"
                    reply_text = str(exc)
                    spoken = reply_text
                    chips = []
                else:
                    workflow_id = workflow["workflow_id"]
                    reply_text = ("Поручение сохранено. Для каждого поста потребуется "
                                  "отдельное подтверждение; публикация не запускается.")
                    spoken = reply_text
                    blocks = [workflow]
                    chips = []
            else:
                outcome = await run_readonly_plan(ctx, decision.steps)
                reply_text, spoken = outcome.text, outcome.spoken or outcome.text
                blocks = outcome.blocks
                if not outcome.ok:
                    intent = "clarify" if not blocks else "plan"
                chips = FOLLOW_UP if blocks else []
        elif decision.intent == "unsupported":
            if not chips and chip is None:
                intent = "clarify"
                reply_text = "Не удалось подобрать подходящее действие. Какой результат вы хотите получить?"
                spoken = reply_text
                chips = [item.model_dump() for item in _fallback_chips()]
            else:
                reply_text = decision.reply_text or "Прямого действия для этой просьбы нет. Вот доступные варианты."
                spoken = reply_text
    blocks = [{"type": "text", "text": reply_text}, *blocks]
    if (decision is not None and decision.intent == "clarify" and intent == "clarify"
            and confirmation is None and outcome is None and decision.clarification_options):
        blocks.append({"type": "clarification", "options": decision.clarification_options})
        chips = []
    confirmation_id = None
    if confirmation is not None:
        confirmation_id = uuid4().hex
        blocks.append(_confirm_block(confirmation, confirmation_id, capability_id))
    elif chips:
        blocks.append({"type": "chips", "items": chips})
    params = {**confirmation.params, "_capability": capability_id} if confirmation is not None else \
        (decision.params if decision else {})
    params = {**params, "_reply_to": str(user.id)}
    if workflow_id:
        params = {**params, "_workflow_id": workflow_id}
    answer = write_message(db, conversation, role="assistant", input_mode="text", text=reply_text, spoken=spoken,
                            blocks=clean_blocks(blocks),
                            intent=intent, capability=capability_id or (decision.capability if decision else None),
                            params=params, confidence=(decision.confidence if decision else None),
                            task_id=outcome.task_id if outcome else None, model=model_name,
                            confirmation_id=confirmation_id, confirmation_state="pending" if confirmation else None)
    if preparing:
        db.commit()
    return {"messages": [to_dict(user), to_dict(answer)], "replayed": False}


def _fallback_chips() -> list[Chip]:
    return [Chip(label="Что в работе", capability="show_work"), Chip(label="Проверить готовое", capability="show_review"),
            Chip(label="Где что находится", capability="go_to", params={"target": "work"})]


# ---- confirmations ----------------------------------------------------------------------

async def edit_confirmation(db: Session, ctx: Context, edit, *, commit: bool = True) -> dict:
    """Edit a content request under the same conversation lock as confirmation.

    Never queues work: only the explicit /confirm endpoint may start the saved draft.
    """
    from app.dispatcher.editable import EDITABLE_CAPABILITIES, target_for_edit, draft_view
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    card = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id,
        DispatcherMessage.confirmation_id == edit.confirmation_id,
        DispatcherMessage.role == "assistant").with_for_update().execution_options(populate_existing=True))
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
    reply_to = (card.params or {}).get("_reply_to")
    card.params = {**described.params, "_capability": capability_id,
                   **({"_reply_to": reply_to} if reply_to else {})}
    card.capability = capability_id
    card.text = "Параметры обновлены. Проверьте и подтвердите создание."
    card.spoken = card.text
    if commit:
        db.commit()
    else:
        db.flush()
    return {"messages": [to_dict(card)], "replayed": False}



async def confirm(db: Session, ctx: Context, confirmation_id: str, *, accept: bool,
                  via: str = "button", revision: int | None = None) -> dict:
    """Answer a confirmation card. A card works once; a voice 'yes' only answers the latest open card."""
    conversation = open_conversation(db, ctx.product.id, ctx.account)
    card = db.scalar(select(DispatcherMessage).where(
        DispatcherMessage.conversation_id == conversation.id, DispatcherMessage.confirmation_id == confirmation_id,
        DispatcherMessage.role == "assistant").with_for_update().execution_options(populate_existing=True))
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
    stored.pop("_reply_to", None)
    card.confirmation_state = "confirmed" if accept else "declined"
    card.decided_at = datetime.now(timezone.utc)
    card.blocks = [({**b, "state": card.confirmation_state} if b.get("type") == "confirm" else b) for b in card.blocks]
    # Consume the user decision durably before any handler that may commit or roll back.
    # A failed or interrupted operation needs a fresh card, never a replay of this approval.
    db.commit()
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
    if capability_id == "repair_video_images":
        return Outcome("Перерисовку не запускаю. Черновик остаётся без платного запуска.")
    if capability_id == "whiteboard_video":
        from app.content.whiteboard.jobs import cancel_job
        try:
            cancel_job(ctx.db, ctx.product.id, UUID(str(stored["task_id"])))
        except (KeyError, ValueError, LookupError):
            ctx.db.rollback()
        return Outcome("Хорошо, видео не запускаю — задание отменено.", "Хорошо, видео не запускаю.")
    return Outcome("Хорошо, ничего не меняю.", "Хорошо, ничего не меняю.")
