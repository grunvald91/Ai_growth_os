"""Paid background draft preparation, with a durable claim and no automatic retry.

Bounded, ordered pagination scans past paused businesses. A fixed LIMIT before checking
per-business permissions can starve every opted-in company after row 500.
Publication is never called from this worker.
"""
import asyncio
import logging

from sqlalchemy import select

from app.comments.models import BusinessComment, CommentChannel
from app.comments.service import CommentService, recent_platform_comment
from app.comments.polling import SUPPORTED

log = logging.getLogger(__name__)
PAGE_SIZE = 500


async def run_comment_draft_once(session_factory, settings, *, llm=None):
    if not settings.comment_background_draft_enabled:
        return 0

    offset = 0
    paused_products = set()
    while True:
        with session_factory() as db:
            query = (
                select(BusinessComment.created_at, BusinessComment.id, BusinessComment.product_id, BusinessComment.target)
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
                .offset(offset)
                .limit(PAGE_SIZE)
            )
            candidates = list(db.execute(query).all())

        if not candidates:
            return 0

        for index, (created_at, comment_id, product_id, target) in enumerate(candidates):
            if index % 50 == 0:
                await asyncio.sleep(0)
            if product_id in paused_products:
                continue
            # Historical imports without an authoritative source timestamp
            # must never burn the auto-draft daily quota.
            from types import SimpleNamespace
            if not recent_platform_comment(
                    SimpleNamespace(target=target), settings.comment_background_draft_max_age_days):
                continue
            with session_factory() as db:
                try:
                    service = CommentService(db, settings, llm=llm)
                    config, _ = service.config(product_id)
                    if not config.enabled or not config.auto_draft:
                        paused_products.add(product_id)
                        continue

                    # Never instantiate a paid provider before the business opts in.
                    if service.llm is None:
                        from app.core.composition import build_llm_provider
                        from app.llm.service import LLMService
                        from app.privacy import PrivacyGateway
                        service.llm = LLMService(
                            privacy_gateway=PrivacyGateway(),
                            # A single paid claim must not silently fan out
                            # to fallback models on 429/network errors.
                            provider=build_llm_provider(settings.model_copy(update={
                                "llm_fallback_models": "", "model_router_enabled": False,
                            })),
                        )
                    await service.draft(product_id, comment_id, background=True)
                    return 1
                except ValueError as exc:
                    db.rollback()
                    log.warning("comment_auto_draft_rejected product=%s comment=%s type=%s",
                                product_id, comment_id, type(exc).__name__)
                    # A failed paid call is already a claim. Never start many
                    # other paid calls in the same tick after this failure.
                    status = db.scalar(select(BusinessComment.status).where(
                        BusinessComment.id == comment_id))
                    if status == "draft_failed":
                        return 0
                except Exception as exc:
                    db.rollback()
                    log.warning("comment_auto_draft_failed product=%s comment=%s type=%s",
                                product_id, comment_id, type(exc).__name__)
                    status = db.scalar(select(BusinessComment.status).where(
                        BusinessComment.id == comment_id))
                    if status in {"draft_failed", "drafting"}:
                        return 0

        offset += len(candidates)
        if len(candidates) < PAGE_SIZE:
            return 0
