"""Opt-in background comment intake; no LLM requests or publishing.

Bounded offset pagination scans beyond paused channels without loading the entire
table or starving a later opted-in business.
"""
import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.comments.models import CommentChannel
from app.comments.service import CommentService

log = logging.getLogger(__name__)
SUPPORTED = ("youtube", "instagram", "vk")
PAGE_SIZE = 500


def poll_due(cursor, current=None):
    current = current or datetime.now(timezone.utc)
    poll = (cursor or {}).get("_poll") or {}
    try:
        scheduled = datetime.fromisoformat(poll["next_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return scheduled.tzinfo is None or scheduled <= current


async def run_comment_poll_once(session_factory, settings, *, gateway=None):
    """Poll one due channel, scanning all pages for enabled companies."""
    if not settings.comment_background_poll_enabled:
        return 0
    offset = 0
    paused_products = set()
    while True:
        with session_factory() as db:
            query = (
                select(CommentChannel.product_id, CommentChannel.platform,
                       CommentChannel.cursor, CommentChannel.updated_at, CommentChannel.id)
                .where(CommentChannel.status == "connected", CommentChannel.platform.in_(SUPPORTED))
                .order_by(CommentChannel.updated_at.asc(), CommentChannel.id.asc())
                .offset(offset)
                .limit(PAGE_SIZE)
            )
            candidates = list(db.execute(query).all())

        if not candidates:
            return 0
        for index, (product_id, platform, cursor, _, _) in enumerate(candidates):
            if index % 50 == 0:
                await asyncio.sleep(0)
            if product_id in paused_products:
                continue
            if not poll_due(cursor):
                continue
            with session_factory() as db:
                try:
                    service = CommentService(db, settings, gateway=gateway)
                    config, _ = service.config(product_id)
                    if not config.enabled or not config.auto_import:
                        paused_products.add(product_id)
                        continue
                    await service.sync(product_id, platform, background=True)
                    return 1
                except Exception as exc:
                    db.rollback()
                    log.warning("comment_poll_failed product=%s platform=%s type=%s",
                                product_id, platform, type(exc).__name__)
        offset += len(candidates)
        if len(candidates) < PAGE_SIZE:
            return 0
