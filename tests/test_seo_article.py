"""Articles written for a search plan item must serve the real query and carry the answer and questions."""
import copy

import pytest
from sqlalchemy import select

from app.models.domain import Artifact
from app.workflows.article_contracts import ArticleArtifact, SeoBrief
from test_articles import ArticleProductionInput, SequenceProvider, build_article_production, Settings, product, valid_outputs  # noqa: F401

BRIEF = SeoBrief(plan_item_id="a001", primary_query="как вести запись клиентов", related_queries=["запись клиентов в салоне"],
                 stage="awareness", intent="informational", reader_problem="Мастер теряет клиентов в переписках.",
                 ai_questions=["как лучше вести запись клиентов мастеру"], next_step="Показать онлайн-запись")

GEO = dict(
    direct_answer="Вести запись клиентов проще всего в одном месте: календаре с онлайн-записью, где клиент сам выбирает услугу и время.",
    meta_description="Как вести запись клиентов без потерь: один календарь, понятные шаги и онлайн-запись для мастера.",
    faq=[dict(question=f"Вопрос {i} про запись клиентов?",
              answer="Короткий самодостаточный ответ на вопрос читателя, который понятен без остального текста статьи.")
         for i in range(3)],
)


def outputs_with_geo(*, final=True):
    outputs = valid_outputs()
    if final:
        outputs[5].update(copy.deepcopy(GEO))
    return outputs


def request_for(product):
    return ArticleProductionInput(product_id=product.id, topic="Как вести запись клиентов", seo_brief=BRIEF)


@pytest.mark.asyncio
async def test_article_serves_plan_query_and_carries_answer_and_faq(db, product, tmp_path):
    provider = SequenceProvider(outputs_with_geo())
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product))
    assert result.status == "completed", result.error
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert article.seo.primary_query == "как вести запись клиентов"  # the plan's real query, not the model's
    assert article.seo_brief.plan_item_id == "a001"
    assert "**Коротко:**" in article.full_article and "## Частые вопросы" in article.full_article
    assert article.reader_article.meta_description and len(article.reader_article.faq) == 3
    geo_roles = {"seo_editor", "article_writer", "human_editor", "marketing_editor"}
    for request in provider.requests:
        has_rules = "ПОИСК И ИИ-ПОМОЩНИКИ" in request.system_prompt
        assert has_rules == (request.metadata["team_role"] in geo_roles)


@pytest.mark.asyncio
async def test_final_editor_missing_answer_is_sent_back_once(db, product, tmp_path):
    outputs = outputs_with_geo(final=False)
    outputs.insert(6, copy.deepcopy(outputs[5]) | copy.deepcopy(GEO))  # the repair answer follows the rejected one
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product))
    assert result.status == "completed", result.error
    assert len(provider.requests) == 11
    assert "direct_answer is required" in provider.requests[6].system_prompt


@pytest.mark.asyncio
async def test_article_without_answer_after_repair_fails_instead_of_publishing_thin_copy(db, product, tmp_path):
    outputs = outputs_with_geo(final=False)
    outputs.insert(6, copy.deepcopy(outputs[5]))  # the repair is still without the answer
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product))
    assert result.status == "failed"
    assert not list(db.scalars(select(Artifact).where(Artifact.kind == "article")))


@pytest.mark.asyncio
async def test_plain_articles_are_unchanged(db, product, tmp_path):
    provider = SequenceProvider(valid_outputs())
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(ArticleProductionInput(product_id=product.id, topic="Booking checklist"))
    assert result.status == "completed", result.error
    assert all("ПОИСК И ИИ-ПОМОЩНИКИ" not in r.system_prompt for r in provider.requests)
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert article.seo_brief is None and "## Частые вопросы" not in article.full_article
