"""Offline checks for one bounded held-article correction with a fresh critic."""
import json

import pytest
from sqlalchemy import select

from app.models.domain import Approval, Artifact, ArtifactStatus, Publication, Task
from app.seo.held_repair import ATTEMPT, run_held_repair_once
from app.seo.publishing import critic_verdict, publish_if_clear
from app.workflows.article_contracts import ArticleArtifact
from test_articles import SequenceProvider, build_article_production, Settings
from test_seo_article import outputs_with_geo, request_for
from test_seo_factory import env  # noqa: F401
from test_critic import finding


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["clean", "critical", "owner_veto", "disabled", "model_wait"])
async def test_held_repair_is_bounded_and_preserves_original_on_failure(env, tmp_path, case):
    factory, settings, product_id = env
    from app.models.domain import Product
    with factory() as db:
        product = db.get(Product, product_id)
        workflow = build_article_production(db, Settings(products_root=tmp_path / "empty"),
                                            SequenceProvider(outputs_with_geo()[:6]))
        result = await workflow.run(request_for(product).model_copy(update={"light": True}))
        assert result.status == "completed", result.error
        task = db.get(Task, result.task_id)
        task.title = "SEO-ARTICLE:test:a001"
        task.assigned_agent = "content_factory"
        row = db.get(Artifact, result.artifact_ids[0])
        article_id = row.id
        original = row.content
        original_article = ArticleArtifact.model_validate_json(original)
        db.commit()
        assert not publish_if_clear(db, article_id).published
        if case == "owner_veto":
            approval = db.scalar(select(Approval).where(Approval.artifact_id == article_id))
            approval.status = "rejected"
            db.commit()
        assert row.status == ArtifactStatus.AWAITING_APPROVAL
    settings = settings.model_copy(update={"seo_held_repair_enabled": case != "disabled"})
    response = original_article.reader_article.model_dump(mode="json")
    response["title"] = "Как организовать запись клиентов без потерь"
    provider = SequenceProvider([response, finding("critical") if case == "critical" else {"findings": []}])
    if case == "model_wait":
        from datetime import datetime, timezone
        from app.llm.seo_availability import ModelDeferred
        async def unavailable(request):
            provider.requests.append(request)
            raise ModelDeferred(datetime.now(timezone.utc).timestamp() + 300)
        provider.generate = unavailable
    assert await run_held_repair_once(factory, settings, provider=provider) == (
        0 if case in {"owner_veto", "disabled"} else 1)
    with factory() as db:
        row = db.get(Artifact, article_id)
        assert not list(db.scalars(select(Publication)))
        if case == "clean":
            candidate = ArticleArtifact.model_validate_json(row.content)
            assert row.status == ArtifactStatus.DRAFT
            assert candidate.reader_article.title == response["title"]
            assert candidate.seo_brief == original_article.seo_brief
            assert candidate.draft == original_article.draft
            assert candidate.conversion == original_article.conversion
            assert critic_verdict(db, row.task_id, candidate.reader_article) == "no_material_issues"
            backup = db.scalar(select(Artifact).where(Artifact.kind == "seo_article_revision_backup"))
            assert backup.content == original
            assert publish_if_clear(db, article_id).published
        else:
            assert row.content == original
            assert row.status == ArtifactStatus.AWAITING_APPROVAL
        markers = list(db.scalars(select(Artifact).where(Artifact.kind == ATTEMPT)))
        assert len(markers) == (0 if case in {"owner_veto", "disabled"} else 1)
        if markers:
            assert json.loads(markers[0].content)["state"] == ("repaired" if case == "clean" else "model_wait" if case == "model_wait" else "failed")
    assert await run_held_repair_once(factory, settings, provider=provider) == 0
    assert len(provider.requests) == (0 if case in {"owner_veto", "disabled"} else 1 if case == "model_wait" else 2)
    if case == "model_wait":
        with factory() as db:
            marker = db.scalar(select(Artifact).where(Artifact.kind == ATTEMPT))
            state = json.loads(marker.content)
            state["retry_at"] = "2020-01-01T00:00:00+00:00"
            marker.content = json.dumps(state)
            db.commit()
        restored = SequenceProvider([response, {"findings": []}])
        assert await run_held_repair_once(factory, settings, provider=restored) == 1
        with factory() as db:
            assert db.get(Artifact, article_id).status == ArtifactStatus.DRAFT
