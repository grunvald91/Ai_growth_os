"""The ServGo corrective release must serve owners and preserve SEO provenance."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.seo.contracts import QueryCluster, SemanticCore, SourceStatus
from app.seo.revise_servgo_owner_articles import (
    CONTENT, REVISION_NOTE, _query_in_collected_core,
)
from app.workflows.article_contracts import HumanEditedArticle


@pytest.mark.parametrize("slug", sorted(CONTENT))
def test_two_owner_pages_are_clear_complete_and_rich(slug):
    info = CONTENT[slug]
    assert info["new_slug"].startswith("/blog/kak-")
    assert info["primary_query"] not in (
        "запись к психологу", "записаться на маникюр онлайн")
    assert "владелец" in info["reader_problem"].casefold()
    reader = HumanEditedArticle.model_validate({
        **{k: info[k] for k in (
            "title", "lead", "direct_answer", "meta_description",
            "sections", "closing", "faq")},
        "language": "ru",
        "fact_renderings": [],
        "editorial_notes": [REVISION_NOTE],
    })
    assert len(reader.sections) >= 4
    body = "\n".join(section.body for section in reader.sections)
    assert "**" in body and "*" in body
    assert "\n### " in body and "\n> " in body
    assert "\n- " in body and "\n1. " in body
    assert "ServGo" in body
    assert "онлайн-запись" in body
    assert len(reader.meta_description) > 60


def test_actual_collected_queries_gate_manual_edit(monkeypatch):
    from app.seo import revise_servgo_owner_articles as script
    core = SemanticCore(
        seeds=["организовать запись клиентов"],
        collected_at=datetime.now(timezone.utc),
        sources=[SourceStatus(engine="google", requests=1, failed_requests=0, queries_found=2)],
        clusters=[
            QueryCluster(id="c001", head_query="как организовать запись клиентов",
                         stage="awareness", intent="informational",
                         queries=["как организовать запись клиентов"], engines=["google"]),
            QueryCluster(id="c002", head_query="программа для записи клиентов в салоне красоты",
                         stage="consideration", intent="commercial",
                         queries=["программа для записи клиентов в салоне красоты"], engines=["google"]),
        ],
    )
    monkeypatch.setattr(script, "latest_semantic_core", lambda db, company_id: core)
    for info in CONTENT.values():
        cluster = _query_in_collected_core(None, None, info["primary_query"])
        assert info["primary_query"] in cluster.queries
    with pytest.raises(ValueError, match="collected"):
        _query_in_collected_core(None, None, "как записаться на маникюр сегодня")


def test_review_and_publication_are_opt_in_atomic():
    import inspect
    from app.seo.revise_servgo_owner_articles import _review, run
    review_code = inspect.getsource(_review)
    commit_code = inspect.getsource(run)
    assert "if not apply:" in review_code
    assert "second_opinion(" in review_code
    assert "PREVIOUS_NOTE" in review_code
    assert "status not in CLEAN_CRITIC_STATUSES" in review_code
    assert "prepared = [await _review" in commit_code
    assert commit_code.index("prepared = [await _review") < commit_code.index("staged = []")
    assert "with_for_update()" in commit_code
    assert "seo_article_revision_backup" in commit_code
    assert "reader_hash" in commit_code
    assert "pub.external_reference = entry[\"new\"]" in commit_code
    assert "db.commit()" in commit_code


def test_published_owner_guides_require_real_critic_even_if_gateway_slow():
    import inspect
    from app.seo import revise_servgo_owner_articles as release
    source = inspect.getsource(release._review)
    assert 90 < release.REVIEW_TIMEOUT_SECONDS <= 300
    assert 120 < release.AUTHOR_REVISION_TIMEOUT_SECONDS <= 240
    assert "review_timeout=REVIEW_TIMEOUT_SECONDS" in source
    assert "revision_timeout=AUTHOR_REVISION_TIMEOUT_SECONDS" in source
    assert "failure_reason" in source  # safe failure class appears in the run log
    assert "raise ValueError(\"Critic rejected owner article:" in source
    assert "status not in CLEAN_CRITIC_STATUSES" in source
    assert "second_opinion(" in source


@pytest.mark.parametrize("slug", sorted(CONTENT))
def test_critic_can_edit_prose_but_cannot_edit_verified_company_facts(slug):
    from app.seo.revise_servgo_owner_articles import (
        EditableOwnerArticle, editable_payload, apply_editorial_copy,
    )
    profile = CONTENT[slug]
    original = HumanEditedArticle.model_validate({
        **{key: profile[key] for key in (
            "title", "lead", "direct_answer", "meta_description",
            "sections", "closing", "faq")},
        "language": "ru",
        "fact_renderings": [
            {"source_id": "source-1",
             "text": "В ServGo администратор может добавить запись вручную."}
        ],
        "editorial_notes": ["prior-reviewed", REVISION_NOTE],
    })
    editable = editable_payload(original)
    assert "fact_renderings" not in editable
    assert "editorial_notes" not in editable
    assert EditableOwnerArticle.model_validate(editable)
    revised = apply_editorial_copy(original, {
        **editable, "title": "Как владельцу организовать рабочую запись клиентов",
        "lead": "Новое описание **понятного процесса** для владельца бизнеса.",
    })
    assert revised.title == "Как владельцу организовать рабочую запись клиентов"
    assert revised.fact_renderings == original.fact_renderings
    assert revised.editorial_notes == original.editorial_notes
    assert revised.lead != original.lead

    # The reviewer contract itself rejects any attempted source substitution.
    with pytest.raises(ValueError):
        apply_editorial_copy(original, {
            **editable, "fact_renderings": [{"source_id": "invented", "text": "Реклама"}]
        })
    with pytest.raises(ValueError):
        apply_editorial_copy(original, {**editable, "direct_answer": ""})


def test_critic_receives_locked_evidence_but_revision_schema_is_editable_only():
    from inspect import getsource
    from app.seo.revise_servgo_owner_articles import _review
    source = getsource(_review)
    assert '"immutable_approved_fact_renderings"' in source
    assert 'response_schema=EditableOwnerArticle.model_json_schema()' in source
    assert "content=json.dumps(editable, ensure_ascii=False)" in source
    assert "apply_editorial_copy(reader, value)" in source
    assert "Critical finding cites immutable verified fact" in source
    assert "status not in CLEAN_CRITIC_STATUSES" in source
