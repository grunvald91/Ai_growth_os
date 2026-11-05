"""SQL state and cross-worker serialization, keyed by (business, authenticated principal)."""

from datetime_utils import utc_now
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import select, text

from database.models import SalesConversation


async def lock_thread(db, business_id: int, principal: str):
    if db.get_bind().dialect.name == "postgresql":
        digest = hashlib.sha256(f"servgo-sales:{business_id}:{principal}".encode()).digest()
        lock_id = int.from_bytes(digest[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})


async def load_thread(db, business_id: int, principal: str, *, create=True):
    await lock_thread(db, business_id, principal)
    conversation = (await db.execute(select(SalesConversation).where(
        SalesConversation.business_id == business_id, SalesConversation.principal == principal)
        .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if conversation is None and create:
        conversation = SalesConversation(id=str(uuid.uuid4()), business_id=business_id,
            principal=principal, messages=[], facts={}, stage="needs_analysis", mode="ai")
        db.add(conversation)
        await db.flush()
    return conversation


def trim_history(history: list[dict], max_turns=15) -> list[dict]:
    # Trim only at user-turn boundaries: assistant tool_calls and ALL their results
    # remain together. Slicing the last N messages produces invalid tool histories.
    starts = [i for i, m in enumerate(history) if m["role"] == "user"]
    return history[starts[-max_turns]:] if len(starts) > max_turns else list(history)


def public_pending(conversation):
    action = conversation.action
    if not action or action.get("status") != "pending":
        return None
    if datetime.fromisoformat(action["expires_at"]) <= utc_now():
        return None
    return {"action_id": conversation.action_id, "tool_name": "create_booking",
            **{k: action[k] for k in ("description", "requires_contact", "requires_legal_consent",
                                    "business_id", "cancellation_policy")},
            "summary": action.get("summary"),
            "expires_at": action["expires_at"]}


def public_slot_offer(conversation):
    if conversation.mode != "ai" or (conversation.action or {}).get("status") == "pending":
        return None
    offer = (conversation.facts or {}).get("_slot_offer")
    if not isinstance(offer, dict) or not offer.get("slots"):
        return None
    if datetime.fromisoformat(offer["expires_at"]) <= utc_now():
        return None
    return {"date": offer["date"], "service_id": offer["service_id"],
            "service_name": offer.get("service_name"), "duration_minutes": offer.get("duration_minutes"),
            "solo": bool(offer.get("solo")), "slots": offer["slots"], "expires_at": offer["expires_at"]}


def visible_history(conversation):
    return [{"id": f"{conversation.id}-{i}", "role": "ai" if m["role"] == "assistant" else "user",
             "text": m["content"]} for i, m in enumerate(conversation.messages)
            if m["role"] in ("user", "assistant") and m.get("content") and not m.get("tool_calls")]
