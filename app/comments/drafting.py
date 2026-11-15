"""Paid background draft preparation, with a durable claim and no automatic retry.

Keyset pagination scans past paused businesses. A fixed LIMIT before checking
per-business permissions can starve every opted-in company after row 500.
Publication is never called from this worker.
"""
import logging

from sqlalchemy import and_, or_, select

from app.comments.models import BusinessComment, CommentChannel
from app.comments.service import CommentService
from app.comments.polling import SUPPORTED

log = logging.getLogger(__name__)
PAGE_SIZE = 500


async def run_comment_draft_once(session_factory, settings, *, llm=None):
    if not settings.comment_background_draft_enabled:
        return 0

    after = None
    while True:
        with session_factory() as db:
            query = (
                select(BusinessComment.created_at, BusinessComment.id, BusinessComment.product_id)
                .join(CommentChannel,
                      (CommentChannel.product_id == BusinessComment.product_id)
                      & (CommentChannel.platform == BusinessComment.platform))
                .where(
                    BusinessComment.status == "new",
                    BusinessComment.source == "platform",
                    BusinessComment.reply_id.is_(None),
                    BusinessComment.platform.in_(SUPPORTED),
                    CommentChannel.status == "connected",
                )
                .order_by(BusinessComment.created_at.asc(), BusinessComment.id.asc())
                .limit(PAGE_SIZE)
            )
            if after is not None:
                timestamp, comment_id = after
                query = query.where(or_(
                    BusinessComment.created_at > timestamp,
                    and_(BusinessComment.created_at == timestamp, BusinessComment.id > comment_id),
                ))
            candidates = list(db.execute(query).all())

        if not candidates:
            return 0

        for created_at, comment_id, product_id in candidates:
            with session_factory() as db:
                try:
                    service = CommentService(db, settings, llm=llm)
                    config, _ = service.config(product_id)
                    if not config.enabled or not config.auto_draft:
                        continue

                    # Never instantiate a paid provider before the business opts in.
                    if service.llm is None:
                        from app.core.composition import build_llm_provider
                        from app.llm.service import LLMService
                        from app.privacy import PrivacyGateway
                        service.llm = LLMService(
                            privacy_gateway=PrivacyGateway(),
                            provider=build_llm_provider(settings),
                        )
                    await service.draft(product_id, comment_id, background=True)
                    return 1
                except ValueError as exc:
                    db.rollback()
                    log.warning("comment_auto_draft_rejected product=%s comment=%s type=%s",
                                product_id, comment_id, type(exc).__name__)
                except Exception as exc:
                    db.rollback()
                    log.warning("comment_auto_draft_failed product=%s comment=%s type=%s",
                                product_id, comment_id, type(exc).__name__)

        after = candidates[-1][0], candidates[-1][1]
        if len(candidates) < PAGE_SIZE:
            return 0
