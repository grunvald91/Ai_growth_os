"""Paid background draft preparation, with a durable claim and no automatic retry.

Each tick selects at most one eligible imported comment; the service re-checks
business/account ownership and budget under its product lock. Publication is
never called by this worker.
"""
import logging

from sqlalchemy import select

from app.comments.models import BusinessComment, CommentChannel
from app.comments.service import CommentService
from app.comments.polling import SUPPORTED

log = logging.getLogger(__name__)


async def run_comment_draft_once(session_factory, settings, *, llm=None):
    if not settings.comment_background_draft_enabled:
        return 0

    with session_factory() as db:
        candidates = list(db.scalars(
            select(BusinessComment)
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
            .order_by(BusinessComment.created_at.asc(), BusinessComment.id)
            .limit(500)
        ))
        identifiers = [(row.product_id, row.id) for row in candidates]

    for product_id, comment_id in identifiers:
        with session_factory() as db:
            try:
                service = CommentService(db, settings, llm=llm)
                config, _ = service.config(product_id)
                if not config.enabled or not config.auto_draft:
                    continue

                # No provider is constructed until a business explicitly opts in.
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
                # Per-comment config/data errors should not block the queue.
                log.warning("comment_auto_draft_rejected product=%s comment=%s type=%s",
                            product_id, comment_id, type(exc).__name__)
            except Exception as exc:
                db.rollback()
                # Do not log LLM responses or credentials.
                log.warning("comment_auto_draft_failed product=%s comment=%s type=%s",
                            product_id, comment_id, type(exc).__name__)
    return 0
