"""Bounded, resumable Instagram nested-reply reconciliation.

Top-level Instagram comment pages do not exhaust nested replies. Scan known
owned-media parents from the existing SQL store, using a durable keyset cursor.
No private API, arbitrary URL, LLM call or publishing happens here.
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.comments.models import BusinessComment

MAX_ROW_SCAN = 50


def _uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _next_parent(db, channel, seek):
    """Find the next eligible top-level comment without loading all tenants."""
    query = (select(BusinessComment)
             .where(BusinessComment.product_id == channel.product_id,
                    BusinessComment.platform == "instagram",
                    BusinessComment.source == "platform")
             .order_by(BusinessComment.id.asc()).limit(MAX_ROW_SCAN))
    if seek:
        query = query.where(BusinessComment.id > seek)
    rows = list(db.scalars(query))
    for row in rows:
        target = row.target or {}
        if (target.get("channel_id") == str(channel.id)
                and str(target.get("media_id") or "").isdigit()
                and str(row.external_id).isdigit()
                and not target.get("parent_id")):
            return row, rows[-1].id, bool(len(rows) == MAX_ROW_SCAN)
    return None, (rows[-1].id if rows else None), bool(len(rows) == MAX_ROW_SCAN)


async def fetch_nested_replies(db, gateway, channel, token, progress=None, *, max_calls=2):
    """Fetch at most max_calls Graph reply pages, resuming via persisted cursor.

    Invalid/deleted parent pointers are skipped on the next scan, never
    silently replaced with a different account. The first cycle may require
    multiple sync ticks to reach a large archive; no arbitrary cutoff on age.
    """
    if max_calls < 1:
        return [], dict(progress or {})

    state = dict(progress or {})
    all_rows = []
    calls = 0
    scanned = 0
    while calls < max_calls and scanned < MAX_ROW_SCAN * 4:
        active = _uuid(state.get("active"))
        parent = db.get(BusinessComment, active) if active else None
        if parent and (parent.product_id != channel.product_id
                       or parent.platform != "instagram"
                       or parent.source != "platform"
                       or (parent.target or {}).get("channel_id") != str(channel.id)
                       or not str((parent.target or {}).get("media_id") or "").isdigit()
                       or not str(parent.external_id).isdigit()
                       or (parent.target or {}).get("parent_id")):
            parent = None

        if parent is None:
            state.pop("active", None)
            state.pop("after", None)
            seek = _uuid(state.get("seek"))
            parent, last, full = _next_parent(db, channel, seek)
            if not parent:
                if last and full:
                    state["seek"] = str(last)
                    scanned += MAX_ROW_SCAN
                    continue
                # One complete cycle is finished. Restart from the oldest
                # parent next tick to catch replies added to older threads.
                return all_rows, {}
            state["active"] = str(parent.id)
            state.pop("after", None)

        target = parent.target or {}
        media_id = str(target["media_id"])
        batch = await gateway.fetch_instagram_replies(
            channel, token, parent.external_id, after=state.get("after")
        )
        calls += 1
        for reply in batch.get("data", []):
            if not isinstance(reply, dict):
                continue
            creator = reply.get("from") or {}
            if not isinstance(creator, dict):
                continue
            author_id = str(creator.get("id") or "")
            # Without a native author ID, we cannot distinguish the business's
            # own replies from customers. Never feed unknown authors to the
            # AI draft queue; they can be reviewed via a manual workflow.
            if not author_id or author_id == str(channel.account_id):
                continue
            identifier = str(reply.get("id") or "")
            if not identifier.isdigit() or not isinstance(reply.get("text"), str) or not reply["text"].strip():
                continue
            all_rows.append({
                "external_id": identifier,
                "text": str(reply["text"]),
                "post_text": (parent.post_text or "")[:8000],
                "target": {
                    "media_id": media_id,
                    "parent_id": parent.external_id,
                    "published_at": reply.get("timestamp"),
                },
            })
        paging = batch.get("paging") or {}
        after = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
        if after:
            state["after"] = after
        else:
            state["seek"] = str(parent.id)
            state.pop("active", None)
            state.pop("after", None)
    return all_rows, state
