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
    assert article.reader_article.direct_answer in article.full_article and "## Частые вопросы" in article.full_article
    assert article.reader_article.meta_description and len(article.reader_article.faq) == 3
    geo_roles = {"seo_editor", "article_writer", "human_editor", "marketing_editor"}
    for request in provider.requests:
        has_rules = "ПОИСК БЕЗ SEO-ПЕРЕСПАМА" in request.system_prompt
        assert has_rules == (request.metadata["team_role"] in geo_roles)
        if has_rules:
            assert "поисковый ИИ-помощник" in request.system_prompt
            assert "Не добавляй искусственные цитаты" in request.system_prompt


@pytest.mark.asyncio
async def test_two_section_article_without_artificial_faq_passes_reader_gate(db, product, tmp_path):
    outputs = outputs_with_geo()
    for index in (4, 5):
        outputs[index]["sections"] = outputs[index]["sections"][:2]
        outputs[index]["faq"] = []
        outputs[index]["editorial_notes"] = []
    provider = SequenceProvider(outputs[:6])
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={"light": True}))
    assert result.status == "completed", result.error
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert len(article.reader_article.sections) == 2 and article.reader_article.faq == []


@pytest.mark.asyncio
async def test_seo_article_keeps_only_topic_relevant_verified_facts(db, product, tmp_path):
    from app.workflows.article_production import product_facts
    root = tmp_path / product.slug / ".claude"
    root.mkdir(parents=True)
    context = ("## Product\n"
               "ServGo помогает владельцам организовать онлайн-запись.\n"
               "Владельцы управляют календарём и услугами.\n"
               "Для другой темы доступны функции ведения смен.\n")
    root.joinpath("product-marketing-context.md").write_text(context, encoding="utf-8")
    facts = product_facts(context)
    assert len(facts) == 3
    selected = next(iter(facts))
    outputs = outputs_with_geo()
    for idx in (4, 5):
        outputs[idx]["fact_renderings"] = [
            {"source_id": selected, "text": "ServGo помогает владельцам организовать онлайн-запись."}
        ]
    workflow = build_article_production(db, Settings(products_root=tmp_path), SequenceProvider(outputs[:6]))
    result = await workflow.run(request_for(product).model_copy(update={"light": True}))
    assert result.status == "completed", result.error
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert [claim.source_id for claim in article.draft.claims] == [selected]
    assert len(article.reader_article.fact_renderings) == 1
    assert "Для другой темы" not in article.full_article


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
async def test_failed_seo_writer_kind_gets_strict_targeted_repair_then_completes(db, product, tmp_path):
    outputs = outputs_with_geo()[:6]
    bad = copy.deepcopy(outputs[2])
    bad['sections'][0]['kind'] = 'fact'
    outputs.insert(2, bad)
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={'light': True}))
    assert result.status == 'completed', result.error
    assert len(provider.requests) == 7
    repair_prompt = provider.requests[3].system_prompt
    assert 'sections.0.kind:literal_error' in repair_prompt
    assert 'kind must be exactly lowercase "advice" or "hypothesis"' in repair_prompt
    assert 'Do not merely relabel an unsupported factual claim' in repair_prompt
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert all(section.kind in {'advice', 'hypothesis', 'fact'} for section in article.draft.sections)


@pytest.mark.asyncio
async def test_seo_writer_repeated_invalid_kind_still_fails_closed(db, product, tmp_path):
    outputs = outputs_with_geo()[:6]
    bad = copy.deepcopy(outputs[2])
    bad['sections'][0]['kind'] = 'fact'
    outputs[2] = bad
    outputs.insert(3, copy.deepcopy(bad))
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={'light': True}))
    assert result.status == 'failed'
    assert not list(db.scalars(select(Artifact).where(Artifact.kind == 'article')))


@pytest.mark.asyncio
async def test_plain_articles_are_unchanged(db, product, tmp_path):
    provider = SequenceProvider(valid_outputs())
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(ArticleProductionInput(product_id=product.id, topic="Booking checklist"))
    assert result.status == "completed", result.error
    assert all("ПОИСК БЕЗ SEO-ПЕРЕСПАМА" not in r.system_prompt for r in provider.requests)
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert article.seo_brief is None and "## Частые вопросы" not in article.full_article


@pytest.mark.asyncio
async def test_light_mode_makes_article_and_questions_only_in_six_calls(db, product, tmp_path):
    provider = SequenceProvider(outputs_with_geo()[:6])
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    request = request_for(product).model_copy(update={"light": True})
    result = await workflow.run(request)
    assert result.status == "completed", result.error
    assert len(provider.requests) == len(result.run_ids) == 6
    assert {r.metadata["team_role"] for r in provider.requests} == {
        "content_strategist", "seo_editor", "article_writer", "conversion_editor", "human_editor", "marketing_editor"}
    kinds = [db.get(Artifact, aid).kind for aid in result.artifact_ids]
    assert kinds == ["article"]  # no social, video, story or image packages
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert article.conversion.derivatives == [] and "## Частые вопросы" in article.full_article
    assert article.seo.primary_query == BRIEF.primary_query


@pytest.mark.asyncio
async def test_light_mode_still_requires_answer_and_questions(db, product, tmp_path):
    outputs = outputs_with_geo(final=False)[:6]
    outputs.insert(6, copy.deepcopy(outputs[5]))
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={"light": True}))
    assert result.status == "failed"


@pytest.mark.parametrize("wrapped", [
    "plain",
    "fence",
    "prose",
    "trailing_comma",
    "literal_newline",
])
def test_seo_plan_parser_recovers_only_unambiguous_model_json(wrapped):
    import json
    from app.workflows.article_production import _parse_stage_payload
    from app.workflows.article_contracts import SEOPlan
    payload = {
        "primary_query": "программа онлайн записи клиентов",
        "related_queries": [],
        "title": "Как выбрать программу",
        "hook": "Проверить календарь и CRM",
        "outline": ["Ручная запись", "Самозапись"],
        "adaptation_notes": "Не обещать неизвестные функции",
    }
    raw = json.dumps(payload, ensure_ascii=False)
    if wrapped == "fence":
        raw = "```json\n" + raw + "\n```"
    elif wrapped == "prose":
        raw = "План SEO:\n" + raw + "\nПроверьте исходные факты."
    elif wrapped == "trailing_comma":
        raw = raw[:-1] + ",}"
    elif wrapped == "literal_newline":
        raw = raw.replace("Проверить календарь и CRM", "Проверить календарь\nи CRM")
    parsed = _parse_stage_payload(SEOPlan, raw)
    assert parsed.primary_query == payload["primary_query"]
    assert len(parsed.outline) == 2


def test_seo_plan_parser_rejects_unclosed_or_multiple_json_objects():
    import json
    from app.workflows.article_production import _parse_stage_payload
    from app.workflows.article_contracts import SEOPlan
    good = {
        "primary_query": "запись клиентов",
        "title": "Система записи клиентов",
        "hook": "Сравнение вариантов",
        "outline": ["Функции"], "adaptation_notes": "Только проверенные факты",
    }
    raw = json.dumps(good, ensure_ascii=False)
    with pytest.raises(ValueError):
        _parse_stage_payload(SEOPlan, raw[:-1])
    with pytest.raises(ValueError):
        _parse_stage_payload(SEOPlan, raw + raw)
    with pytest.raises(ValueError):
        _parse_stage_payload(SEOPlan, '{"primary_query":"запись клиентов"}')


@pytest.mark.asyncio
async def test_invalid_seo_strategist_recovers_only_from_verified_plan_brief(db, product, tmp_path):
    """Two malformed responses cannot erase an approved SEO article plan."""
    outputs = [{}, {}, *outputs_with_geo()[:6][1:]]
    provider = SequenceProvider(outputs)
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={"light": True}))
    assert result.status == "completed", result.error
    article = ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content)
    assert article.strategy.pain == BRIEF.reader_problem
    assert article.strategy.search_intent.find(BRIEF.primary_query) >= 0
    assert article.seo_brief.primary_query == BRIEF.primary_query
    checkpoints = list(db.scalars(select(Artifact).where(
        Artifact.task_id == result.task_id, Artifact.kind == "content_stage_output")))
    saved = [__import__("json").loads(a.content) for a in checkpoints]
    strategist = next(x for x in saved if x["stage"] == "content_strategist")
    assert strategist["fallback_used"] is True
    assert strategist["fallback_reason"] == "verified_seo_brief_after_invalid_strategist_output"
    assert len(provider.requests) == 7  # two failed attempts, then normal downstream stages


@pytest.mark.asyncio
async def test_strategist_fallback_never_applies_to_unplanned_articles(db, product, tmp_path):
    provider = SequenceProvider([{}, {}])
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(ArticleProductionInput(product_id=product.id, topic="Без SEO-плана"))
    assert result.status == "failed"
    assert not list(db.scalars(select(Artifact).where(Artifact.kind == "article")))


@pytest.mark.asyncio
async def test_invalid_seo_strategist_does_not_silently_recover_truncated_repair(db, product, tmp_path):
    """Only two parsed validation rejections may trigger the approved brief fallback."""
    # First answer fails schema validation; the second repair is oversized,
    # so it was not actually validated as a usable strategy.
    provider = SequenceProvider([{}, "x" * 150001])
    workflow = build_article_production(db, Settings(products_root=tmp_path), provider)
    result = await workflow.run(request_for(product).model_copy(update={"light": True}))
    assert result.status == "failed"
    assert result.error.startswith("invalid_article_output:content_strategist")
    assert not list(db.scalars(select(Artifact).where(Artifact.kind == "article")))
