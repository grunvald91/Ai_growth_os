"""Opt-in background comment intake. No LLM calls and no publishing.

Single bounded fetch per tick; channel/product locks and persisted cursor state in
CommentService.sync prevent duplicate imports across workers/restarts. Operators must
enable the global switch; businesses must separately enable auto_import.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.comments.models import CommentChannel
from app.comments.service import CommentService

log = logging.getLogger(__name__)
SUPPORTED = ("youtube", "instagram")


def poll_due(cursor, current=None):
    current = current or datetime.now(timezone.utc)
    poll = (cursor or {}).get("_poll") or {}
    try:
        scheduled = datetime.fromisoformat(poll["next_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return scheduled.tzinfo is None or scheduled <= current


async def run_comment_poll_once(session_factory, settings, *, gateway=None):
    """Poll at most one due channel. Nothing happens unless both opt-ins are on."""
    if not settings.comment_background_poll_enabled:
        return 0
    with session_factory() as db:
        candidates = list(db.execute(
            select(CommentChannel.product_id, CommentChannel.platform, CommentChannel.cursor)
            .where(CommentChannel.status == "connected", CommentChannel.platform.in_(SUPPORTED))
            .order_by(CommentChannel.updated_at.asc(), CommentChannel.id)
            .limit(500)
        ).all())
    for product_id, platform, cursor in candidates:
        if not poll_due(cursor):
            continue
        with session_factory() as db:
            try:
                service = CommentService(db, settings, gateway=gateway)
                config, _ = service.config(product_id)
                if not config.enabled or not config.auto_import:
                    continue
                # This method locks the product and channel, then rechecks due time.
                await service.sync(product_id, platform, background=True)
                return 1
            except Exception as exc:
                db.rollback()
                # Never log arbitrary provider messages (which may contain credentials).
                log.warning("comment_poll_failed product=%s platform=%s type=%s",
                            product_id, platform, type(exc).__name__)
    return 0
