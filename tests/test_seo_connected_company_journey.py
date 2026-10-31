"""One activated ServGo business: profile -> suggestions -> SEO plan -> article -> critic -> blog.

Deterministic fake search and language-model responses. No paid calls, network,
demo-company activation or production writes.
"""
import json
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.composition import build_article_production
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.fake import LLMResponse, LLMUsage
from app.main import app
from app.models.domain import AgentRun, Artifact, ArtifactStatus, Product, Publication, Task, TaskStatus
from app.seo.activation import is_enabled, set_enabled
from app.seo.factory import queue_factory, run_factory_once
from app.seo.publishing import publish_if_clear, reader_hash
from app.sso.models import StudioBusinessLink
from app.workflows.article_contracts import ArticleArtifact
from app.content import worker as content_worker
from test_articles import SequenceProvider, valid_outputs


QUERY = "как выбрать букет в подарок"


class PlannerProvider:
    def __init__(self):
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        role = request.metadata["team_role"]
        if role == "semantic_researcher":
            result = {"seeds": ["букет в подарок", "как выбрать букет", "цветы в подарок"]}
        else:
            assert role == "seo_editor"
            data = json.loads(request.messages[0].content)
            assert "Цветочная мастерская" in data["company"]["name"]
            cluster = next(c for c in data["clusters"] if QUERY in c["queries"])
            result = {"items": [{
                "id": "a001",
                "cluster_ids": [cluster["id"]],
                "primary_query": QUERY,
                "working_title": "Как выбрать букет в подарок и не гадать на лепестках",
                "stage": cluster["stage"],
                "intent": cluster["intent"],
                "reader_problem": "Хочется выбрать уместный букет, но непонятно, с чего начать.",
                "ai_questions": ["Что учесть, выбирая цветы для человека?"],
                "next_step": "Посмотреть букеты и возможность заказа в цветочной мастерской",
            }]}
        return LLMResponse(content=json.dumps(result, ensure_ascii=False), model="fixture",
                           usage=LLMUsage(), stop_reason="end_turn")


def article_outputs():
    outputs = valid_outputs()
    outputs[0]["audience"] = "Люди, выбирающие цветы в подарок"
    outputs[1].update(
        primary_query=QUERY,
        related_queries=["какой букет подарить"],
        title="Как выбрать букет в подарок и не гадать на лепестках",
        hook="Начните с человека, а не с витрины",
    )
    outputs[2] = {
        "sections": [
            {"heading": "Начните с человека", "kind": "advice",
             "body": (
                 "Вспомните, чем этот человек обычно окружает себя дома. Он любит яркие оттенки "
                 "или выбирает спокойные вещи? Есть ли у него место для большой композиции? "
                 "Ответы помогут выбрать направление без поиска универсального «правильного» букета. "
                 "Не нужно угадывать тайное значение каждого цветка: лучше подумать о конкретном "
                 "человеке, его привычках и поводе. Даже простой вопрос о любимых цветах иногда "
                 "полезнее десятка модных подборок."
             )},
            {"heading": "Подумайте о дне вручения", "kind": "advice",
             "body": (
                 "Представьте, как подарок проведёт первые часы после вручения. Его будут нести "
                 "по городу, ставить на праздничный стол или сразу привезут домой? "
                 "Если хочется чего-то выразительного, можно выбрать заметный акцент. "
                 "Если подарок маленький и личный, лаконичное сочетание тоже может выглядеть "
                 "внимательно и тепло. Важно не собрать максимум разных цветов, а передать "
                 "настроение встречи и оставить человеку приятное воспоминание."
             )},
        ],
        "proof_needed_notes": [],
    }
    reader = {
        "language": "ru",
        "title": "Как выбрать букет в подарок, чтобы он говорил о вашем внимании",
        "lead": (
            "Стоите перед витриной, а глаза разбегаются: нежные букеты, яркие, огромные, "
            "совсем маленькие. Хочется выбрать тот самый, но от обилия вариантов легче "
            "не становится. Попробуйте начать не с лепестков — с человека, которому "
            "вы собираетесь подарить цветы."
        ),
        "fact_renderings": [],
        "sections": [
            {"heading": "У букета есть адресат — начните с него",
             "body": (
                 "Вспомните, какие вещи человек выбирает сам. Возможно, он любит спокойные "
                 "оттенки, а может, в его доме всё яркое и необычное. Подумайте и о привычках: "
                 "кто-то с удовольствием ставит цветы в большую вазу, а кто-то живёт в "
                 "небольшой квартире, где каждый сантиметр уже занят. Здесь нет волшебной "
                 "формулы: в одном случае уместна выразительная композиция, в другом — "
                 "несколько цветов с настроением. Когда вы отталкиваетесь от человека, "
                 "вы перестаёте соревноваться с витриной и выбираете подарок с характером. "
                 "И даже если любимый сорт неизвестен, это не мешает проявить внимание."
            )},
            {"heading": "Представьте момент, когда цветы окажутся в руках",
             "body": (
                 "Подарок будет ждать долгую прогулку по городу или сразу окажется дома? "
                 "Его вручат на шумном празднике или во время тихой встречи за кофе? "
                 "Один и тот же букет в этих обстоятельствах ощущается по-разному. "
                 "Для прогулки бывает удобнее компактный формат, для торжества хочется "
                 "чуть больше выразительности. Можно сказать флористу не только повод, "
                 "но и одну простую деталь о получателе: «она любит спокойные оттенки» "
                 "или «он всегда замечает необычные сочетания». Это разговор о человеке, "
                 "а не экзамен по названиям цветов. Хороший выбор начинается с такого "
                 "небольшого, но личного уточнения."
            )},
        ],
        "closing": (
            "Не пытайтесь найти единственный идеальный букет для всех. Пара внимательных "
            "наблюдений о человеке даст больше, чем список популярных цветов. "
            "А если хочется посмотреть сочетания вместе со специалистом, "
            "можно начать с каталога и рассказать о поводе."
        ),
        "editorial_notes": [],
        "direct_answer": (
            "Выбирайте букет в подарок с учётом вкусов человека, повода и того, как "
            "цветы будут вручать. Не обязательно знать названия сортов: несколько "
            "наблюдений о получателе помогут выбрать настроение композиции."
        ),
        "meta_description": (
            "Как выбрать букет в подарок: начните с получателя, подумайте о поводе "
            "и найдите сочетание, которое передаст ваше внимание."
        ),
        "faq": [],  # No fabricated three-question FAQ.
    }
    outputs[4] = dict(reader)
    outputs[5] = dict(reader)
    outputs[3]["cta"] = "Посмотрите букеты мастерской и выберите удобный способ заказа"
    outputs[3]["landing_page"] = dict(label="Букеты мастерской", destination="/business/64")
    return outputs[:6]


@pytest.mark.asyncio
async def test_connected_company_article_reaches_own_blog_with_critic_and_demo_stays_off(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sf = lambda: Session(engine, expire_on_commit=False)
    settings = Settings(products_root=tmp_path / "products", _env_file=None)
    with sf() as db:
        company = Product(name="Цветочная мастерская Лепесток", slug="lepetok",
                          description="Букеты на заказ. Студия в Сочи.")
        demo = Product(name="Демонстрационный салон", slug="demo-lepetok")
        db.add_all([company, demo])
        db.flush()
        db.add(StudioBusinessLink(servgo_business_id=64, product_id=company.id))
        db.commit()
        company_id, demo_id = company.id, demo.id
        assert not is_enabled(db, company_id) and not is_enabled(db, demo_id)
        root = settings.products_root / company.slug / ".claude"
        root.mkdir(parents=True)
        (root / "product-marketing-context.md").write_text(
            "Цветочная мастерская Лепесток, Сочи. Услуга: букеты на заказ. "
            "Тон: тёплый, доброжелательный, живой. Нет обещаний результата.",
            encoding="utf-8",
        )
        set_enabled(db, company_id, enabled=True, actor="owner")
        factory_task = queue_factory(db, company_id)
        assert factory_task.status == TaskStatus.QUEUED
        assert not is_enabled(db, demo_id)

    provider = PlannerProvider()
    def suggestions(request):
        part = request.url.params.get("part") or request.url.params.get("q")
        return httpx.Response(200, text=json.dumps([part, [QUERY]], ensure_ascii=False))
    async with httpx.AsyncClient(transport=httpx.MockTransport(suggestions)) as client:
        assert await run_factory_once(sf, settings, provider=provider, client=client) == 1
    assert {r.metadata["team_role"] for r in provider.requests} == {"semantic_researcher", "seo_editor"}
    with sf() as db:
        assert db.get(Task, factory_task.id).status == TaskStatus.COMPLETED
        article_job = db.scalar(select(Task).where(Task.product_id == company_id,
             Task.assigned_agent == "content_factory", Task.title.like("SEO-ARTICLE:%")))
        assert article_job is not None and article_job.status == TaskStatus.QUEUED
        assert db.scalar(select(Task).where(Task.product_id == demo_id)) is None

    generated = SequenceProvider(article_outputs())
    monkeypatch.setattr(
        content_worker, "build_article_production",
        lambda db, settings: build_article_production(db, settings, generated),
    )
    assert await content_worker.run_content_once(sf, settings) == 1
    with sf() as db:
        article = db.scalar(select(Artifact).where(Artifact.product_id == company_id, Artifact.kind == "article"))
        assert article is not None
        content = ArticleArtifact.model_validate_json(article.content)
        assert len(content.reader_article.sections) == 2
        assert content.reader_article.faq == []
        assert content.seo_brief.primary_query == QUERY
        assert "ServGo" not in content.full_article
        assert "Стоите перед витриной" in content.full_article
        assert "У букета есть адресат" in content.full_article
        assert article.status == ArtifactStatus.AWAITING_APPROVAL
        assert not list(db.scalars(select(Publication)))
        reviewer = db.scalar(select(AgentRun).where(AgentRun.task_id == article.task_id,
            AgentRun.agent_name == "marketing_editor"))
        assert reviewer is not None
        # The fake model gave no clean critic verdict. It must not auto-publish.
        review = db.scalar(select(Artifact).where(
            Artifact.agent_run_id == reviewer.id, Artifact.kind == "seo_final_review"))
        assert review is not None
        review.content = json.dumps({"status": "no_material_issues",
                                     "reader_hash": reader_hash(content.reader_article)})
        db.commit()
        outcome = publish_if_clear(db, article.id)
        assert outcome.published and outcome.slug
        assert db.get(Artifact, article.id).status == ArtifactStatus.PUBLISHED
        assert db.scalar(select(Publication).where(Publication.product_id == demo_id)) is None

        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as api:
                feed = api.get("/public/blog/businesses/64/articles")
                assert feed.status_code == 200
                assert len(feed.json()["articles"]) == 1
                slug = feed.json()["articles"][0]["slug"]
                detail = api.get(f"/public/blog/businesses/64/articles/{slug}")
                assert detail.status_code == 200
                result = detail.json()
                assert result["title"] == content.reader_article.title
                assert len(result["sections"]) == 2 and result["faq"] == []
                assert result["company_url"] == "/business/64"
                assert result["booking_url"] == "/book/64"
                assert result["cta_url"] == "/book/64"
                assert api.get("/public/blog/businesses/65/articles").status_code == 404
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
