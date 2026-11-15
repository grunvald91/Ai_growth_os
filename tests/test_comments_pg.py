"""Real PostgreSQL comment-claim concurrency; never calls external model APIs.

Run with COMMENT_PG_TEST_URL pointed to a DISPOSABLE database. SQLite cannot
exercise row-level locks, which protect the per-business budget and claims.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import sessionmaker

from app.comments.contracts import AgentSettings
from app.comments.models import BusinessComment, CommentChannel
from app.comments.service import CommentService
from app.core.config import Settings
from app.db.models import Base
from app.llm.schemas import LLMResponse, LLMUsage
from app.models.domain import AgentRun, Product, Publication, Task

pytestmark = pytest.mark.asyncio


@pytest.fixture(scope="module")
def pg_factory():
    url = os.environ.get("COMMENT_PG_TEST_URL")
    if not url:
        pytest.skip("Set COMMENT_PG_TEST_URL for live PostgreSQL tests")
    if not url.startswith("postgresql+psycopg://"):
        raise RuntimeError("Only disposable PostgreSQL psycopg databases are allowed")
    engine = create_engine(url, pool_pre_ping=True, pool_timeout=5)
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def setup_company(pg_factory, tmp_path, *, limit=20, comments=1):
    slug = "comment-pg-" + uuid4().hex[:12]
    home = tmp_path / slug
    home.mkdir(parents=True)
    (home / "company-profile.json").write_text(json.dumps({
        "schema_version": "1.0", "identity": {"name": slug}}))
    settings = Settings(_env_file=None, products_root=tmp_path,
                        comment_background_draft_enabled=True,
                        comment_background_draft_daily_limit=limit)
    with pg_factory() as db:
        product = Product(name="Comment PG test", slug=slug)
        db.add(product)
        db.flush()
        company_id = product.id
        channel = CommentChannel(product_id=company_id, platform="youtube",
                                 account_id="UCbusiness", account_name="Test",
                                 status="connected", cursor={})
        db.add(channel)
        db.flush()
        for i in range(comments):
            db.add(BusinessComment(product_id=company_id, platform="youtube",
                external_id="pg-" + uuid4().hex, text="Цена?", post_text="Вопрос",
                source="platform", status="new",
                target={"channel_id": str(channel.id),
                        "published_at": datetime.now(timezone.utc).isoformat()}))
        db.commit()
        CommentService(db, settings).configure(company_id, AgentSettings(auto_draft=True))
        ids = list(db.scalars(select(BusinessComment.id).where(
            BusinessComment.product_id == company_id).order_by(BusinessComment.id)))
    return settings, company_id, ids


class HeldLLM:
    def __init__(self):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, _):
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return LLMResponse(model="fake", usage=LLMUsage(input_tokens=120, output_tokens=70),
            content=json.dumps({"intent": "question", "action": "draft", "reply": "Уточните задачу.",
                "reason": "Конкретный запрос", "used_fact_ids": ["profile.identity"],
                "missing_information": []}))


async def generate(pg_factory, settings, company_id, comment_id, llm):
    with pg_factory() as db:
        return await CommentService(db, settings, llm=llm).draft(
            company_id, comment_id, background=True)


async def test_claim_committed_before_model_and_second_connection_rejected(pg_factory, tmp_path):
    settings, company_id, (comment_id,) = setup_company(pg_factory, tmp_path)
    llm = HeldLLM()
    running = asyncio.create_task(generate(pg_factory, settings, company_id, comment_id, llm))
    try:
        await asyncio.wait_for(llm.started.wait(), timeout=8)
        with pg_factory() as db:
            assert db.get(BusinessComment, comment_id).status == "drafting"
            assert db.scalar(select(Task).where(Task.product_id == company_id)) is not None
        with pg_factory() as other:
            with pytest.raises(ValueError, match="обрабатывается"):
                await CommentService(other, settings, llm=llm).draft(
                    company_id, comment_id, background=True)
            other.rollback()
        assert llm.calls == 1
    finally:
        llm.release.set()
        await running
    with pg_factory() as db:
        assert db.get(BusinessComment, comment_id).status == "draft"
        assert db.scalar(select(AgentRun).join(Task).where(Task.product_id == company_id)).status == "completed"
        assert not list(db.scalars(select(Publication).where(Publication.product_id == company_id)))


async def test_daily_quota_serialized_while_provider_is_in_flight(pg_factory, tmp_path):
    settings, company_id, ids = setup_company(pg_factory, tmp_path, limit=1, comments=2)
    llm = HeldLLM()
    running = asyncio.create_task(generate(pg_factory, settings, company_id, ids[0], llm))
    try:
        await asyncio.wait_for(llm.started.wait(), timeout=8)
        with pg_factory() as other:
            with pytest.raises(ValueError, match="Дневной лимит"):
                await CommentService(other, settings, llm=llm).draft(
                    company_id, ids[1], background=True)
            other.rollback()
        assert llm.calls == 1
    finally:
        llm.release.set()
        await running


async def test_owner_reset_blocks_late_postgres_result(pg_factory, tmp_path):
    settings, company_id, (comment_id,) = setup_company(pg_factory, tmp_path)
    llm = HeldLLM()
    running = asyncio.create_task(generate(pg_factory, settings, company_id, comment_id, llm))
    try:
        await asyncio.wait_for(llm.started.wait(), timeout=8)
        with pg_factory() as owner:
            owner.execute(update(BusinessComment).where(BusinessComment.id == comment_id)
                          .values(updated_at=datetime.now(timezone.utc) - timedelta(minutes=16)))
            owner.commit()
            CommentService(owner, settings).recover_stalled_draft(company_id, comment_id)
    finally:
        llm.release.set()
        with pytest.raises(ValueError, match="Не удалось подготовить"):
            await running
    with pg_factory() as db:
        comment = db.get(BusinessComment, comment_id)
        assert comment.status == "draft_failed" and comment.reply_id is None
        assert not list(db.scalars(select(Publication).where(Publication.product_id == company_id)))
