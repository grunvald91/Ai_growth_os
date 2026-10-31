"""All SEO articles remain private until a human approves the reviewed version."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import AgentRun, Approval, Artifact, ArtifactStatus, Product, Publication
from app.seo.publishing import publish_if_clear, publish_owner_approved, slugify, reader_hash
from app.seo.activation import set_enabled
from app.sso.models import StudioBusinessLink
from app.workflows.article_contracts import ArticleArtifact
from test_articles import SequenceProvider, build_article_production, Settings
from test_seo_article import BRIEF, outputs_with_geo, request_for


@pytest.fixture
def env():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="ServGo", slug="servgo")
        db.add(product)
        db.commit()
        set_enabled(db, product.id, enabled=True, actor='fixture-owner')
        yield db, product
    engine.dispose()


async def make_article(db, product, tmp_path, critic_status="no_material_issues", geo=True):
    outputs = outputs_with_geo(final=geo)
    workflow = build_article_production(db, Settings(products_root=tmp_path), SequenceProvider(outputs))
    result = await workflow.run(request_for(product))
    assert result.status == "completed", result.error
    if critic_status:
        run = db.scalar(select(AgentRun).where(AgentRun.task_id == result.task_id, AgentRun.agent_name == "marketing_editor"))
        review = db.scalar(select(Artifact).where(Artifact.agent_run_id == run.id,
                                                  Artifact.kind == "seo_final_review"))
        review.content = json.dumps({"status": critic_status, "reader_hash": reader_hash(
            ArticleArtifact.model_validate_json(db.get(Artifact, result.artifact_ids[0]).content).reader_article)})
        db.commit()
    return result.artifact_ids[0]


def owner_publish(db, product, tmp_path, article_id):
    from app.content.review import approve_material
    outcome = publish_if_clear(db, article_id)
    assert not outcome.published and outcome.reason == "human_review_required"
    assert not list(db.scalars(select(Publication).where(Publication.artifact_id == article_id)))
    approve_material(db, tmp_path, product.id, article_id)
    assert publish_owner_approved(db) == 1
    return db.scalar(select(Publication).where(Publication.artifact_id == article_id))


def test_slugify_transliterates_russian_to_url_safe_text():
    assert slugify("Как вести запись клиентов?") == "kak-vesti-zapis-klientov"
    assert slugify("ё!!") == "e"
    assert slugify("???") == "article"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["no_material_issues", "revised"])
async def test_clean_critic_still_requires_real_owner_approval(env, tmp_path, status):
    db, product = env
    article_id = await make_article(db, product, tmp_path, status)
    outcome = publish_if_clear(db, article_id)
    assert not outcome.published and outcome.reason == "human_review_required"
    assert db.get(Artifact, article_id).status == ArtifactStatus.AWAITING_APPROVAL
    approval = db.scalar(select(Approval).where(Approval.artifact_id == article_id))
    assert approval.status == "pending" and approval.decided_by is None
    assert not list(db.scalars(select(Publication)))
    assert publish_owner_approved(db) == 0
    from app.content.review import approve_material
    approve_material(db, tmp_path, product.id, article_id)
    assert publish_owner_approved(db) == 1
    publication = db.scalar(select(Publication))
    assert publication.status == "published" and publication.external_reference == "/blog/kak-vesti-zapis-klientov"
    assert db.get(Approval, publication.approval_id).decided_by == "owner:studio"
    assert db.get(Artifact, article_id).status == ArtifactStatus.PUBLISHED
    assert publish_if_clear(db, article_id).reason == "already_published"
    assert publish_owner_approved(db) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [None, "all_rejected", "review_unavailable", "skipped_size_limit", "revision_unavailable"])
async def test_without_clean_critic_review_the_owner_decides(env, tmp_path, status):
    db, product = env
    article_id = await make_article(db, product, tmp_path, status)
    outcome = publish_if_clear(db, article_id)
    assert not outcome.published and "critic review" in outcome.reason
    assert db.get(Artifact, article_id).status == ArtifactStatus.AWAITING_APPROVAL
    approvals = list(db.scalars(select(Approval)))
    assert len(approvals) == 1 and approvals[0].status == "pending"
    assert not list(db.scalars(select(Publication)))
    publish_if_clear(db, article_id)  # asking again does not pile up approvals
    assert len(list(db.scalars(select(Approval)))) == 1


@pytest.mark.asyncio
async def test_same_query_gets_distinct_slug(env, tmp_path):
    db, product = env
    first = owner_publish(db, product, tmp_path, await make_article(db, product, tmp_path))
    second = owner_publish(db, product, tmp_path, await make_article(db, product, tmp_path))
    assert (first.external_reference, second.external_reference) == ("/blog/kak-vesti-zapis-klientov", "/blog/kak-vesti-zapis-klientov-2")


@pytest.mark.asyncio
async def test_public_feed_shows_only_published_articles(env, tmp_path):
    db, product = env
    published = await make_article(db, product, tmp_path)
    held = await make_article(db, product, tmp_path, critic_status=None)
    owner_publish(db, product, tmp_path, published)
    publish_if_clear(db, held)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            listing = client.get("/public/blog/servgo/articles")
            assert listing.status_code == 200
            assert [a["slug"] for a in listing.json()["articles"]] == ["kak-vesti-zapis-klientov"]
            full = client.get("/public/blog/servgo/articles/kak-vesti-zapis-klientov").json()
            assert full["direct_answer"] and len(full["faq"]) == 3 and full["stage"] == BRIEF.stage
            assert all(s["paragraphs"] or s["bullets"] for s in full["sections"])
            assert full["primary_query"] == BRIEF.primary_query
            assert client.get("/public/blog/servgo/articles/kak-vesti-zapis-klientov-2").status_code == 404
            assert client.get("/public/blog/unknown/articles").status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_held_article_goes_live_after_the_owner_approves_it(env, tmp_path):
    from app.content.review import approve_material
    from app.seo.publishing import publish_owner_approved
    db, product = env
    article_id = await make_article(db, product, tmp_path)
    publish_if_clear(db, article_id)
    assert publish_owner_approved(db) == 0  # still waiting for the owner
    approve_material(db, tmp_path, product.id, article_id)
    assert publish_owner_approved(db) == 1
    publication = db.scalar(select(Publication))
    assert publication.external_reference == "/blog/kak-vesti-zapis-klientov"
    assert db.get(Artifact, article_id).status == ArtifactStatus.PUBLISHED
    assert db.get(Approval, publication.approval_id).decided_by == "owner:studio"
    assert publish_owner_approved(db) == 0  # once


@pytest.mark.asyncio
async def test_human_approval_cannot_override_unclean_critic_or_changed_article(env, tmp_path):
    from app.content.review import approve_material
    db, product = env
    article_id = await make_article(db, product, tmp_path, critic_status="all_rejected")
    assert not publish_if_clear(db, article_id).published
    approve_material(db, tmp_path, product.id, article_id)
    assert publish_owner_approved(db) == 0
    assert not list(db.scalars(select(Publication)))
    approval = db.scalar(select(Approval).where(Approval.artifact_id == article_id))
    assert "critic review" in approval.reason

    clean_id = await make_article(db, product, tmp_path)
    assert not publish_if_clear(db, clean_id).published
    approve_material(db, tmp_path, product.id, clean_id)
    row = db.get(Artifact, clean_id)
    data = json.loads(row.content)
    data["reader_article"]["lead"] += " Несогласованное изменение."
    row.content = json.dumps(data, ensure_ascii=False)
    db.commit()
    assert publish_owner_approved(db) == 0
    assert not list(db.scalars(select(Publication)))


@pytest.mark.asyncio
async def test_rejected_article_stays_off_the_blog(env, tmp_path):
    from app.seo.publishing import publish_owner_approved
    db, product = env
    article_id = await make_article(db, product, tmp_path, None)
    publish_if_clear(db, article_id)
    approval = db.scalar(select(Approval))
    approval.status, approval.decided_by = "rejected", "owner"
    db.commit()
    assert publish_owner_approved(db) == 0 and not list(db.scalars(select(Publication)))



@pytest.mark.asyncio
async def test_changed_reader_cannot_use_old_clean_review(env, tmp_path):
    db, product = env
    article_id = await make_article(db, product, tmp_path)
    row = db.get(Artifact, article_id)
    data = json.loads(row.content)
    data["reader_article"]["lead"] += " Изменённый текст."
    row.content = json.dumps(data)
    db.commit()
    outcome = publish_if_clear(db, article_id)
    assert not outcome.published and "content_mismatch" in outcome.reason
    assert not list(db.scalars(select(Publication)))


@pytest.mark.asyncio
async def test_publication_is_retried_after_transient_failure(env, tmp_path, monkeypatch):
    import app.seo.publishing as module
    from app.models.domain import Task
    db, product = env
    article_id = await make_article(db, product, tmp_path)
    row = db.get(Artifact, article_id)
    db.get(Task, row.task_id).title = "SEO-ARTICLE:retry"
    db.commit()
    original = module._go_live
    def unavailable(*args):
        raise RuntimeError("temporary failure")
    monkeypatch.setattr(module, "_go_live", unavailable)
    from app.content.review import approve_material
    publish_if_clear(db, article_id)
    approve_material(db, tmp_path, product.id, article_id)
    with pytest.raises(RuntimeError, match="temporary failure"):
        module.publish_owner_approved(db)
    db.rollback()
    monkeypatch.setattr(module, "_go_live", original)
    assert module.publish_owner_approved(db) == 1
    assert db.get(Artifact, article_id).status == ArtifactStatus.PUBLISHED
    assert module.publish_owner_approved(db) == 0
    assert len(list(db.scalars(select(Publication)))) == 1


@pytest.mark.asyncio
async def test_public_article_shows_fact_renderings_as_reader_prose(env, tmp_path):
    from app.api.public_blog import _article
    db, product = env
    article_id = await make_article(db, product, tmp_path)
    owner_publish(db, product, tmp_path, article_id)
    row = db.get(Artifact, article_id)
    data = json.loads(row.content)
    data["reader_article"]["fact_renderings"] = [
        {"source_id": "catalog-service", "text": "В этой мастерской собирают букеты на заказ."}
    ]
    row.content = json.dumps(data, ensure_ascii=False)
    db.commit()
    publication = db.scalar(select(Publication).where(Publication.artifact_id == article_id))
    public = _article(row, publication)
    assert public["company_facts"] == ["В этой мастерской собирают букеты на заказ."]
    assert "source_id" not in str(public["company_facts"])


@pytest.mark.asyncio
async def test_business_blog_uses_verified_link_and_own_booking_route(env, tmp_path):
    db, product = env
    second = Product(name="Other business", slug="company-other")
    db.add_all([second, StudioBusinessLink(servgo_business_id=64, product_id=product.id)])
    db.flush()
    db.add(StudioBusinessLink(servgo_business_id=65, product_id=second.id))
    db.commit()
    published = await make_article(db, product, tmp_path)
    held = await make_article(db, product, tmp_path, critic_status=None)
    owner_publish(db, product, tmp_path, published)
    publish_if_clear(db, held)

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            directory = client.get("/public/blog/businesses/with-articles")
            assert directory.status_code == 200
            assert directory.json()["business_ids"] == [64]
            listing = client.get("/public/blog/businesses/64/articles")
            assert listing.status_code == 200
            data = listing.json()
            assert data["company_url"] == "/business/64"
            assert data["booking_url"] == "/book/64"
            assert [a["slug"] for a in data["articles"]] == ["kak-vesti-zapis-klientov"]
            full = client.get("/public/blog/businesses/64/articles/kak-vesti-zapis-klientov")
            assert full.status_code == 200
            assert full.json()["cta_url"] == "/book/64"
            assert full.json()["company_url"] == "/business/64"
            assert client.get("/public/blog/businesses/64/articles/kak-vesti-zapis-klientov-2").status_code == 404
            assert client.get("/public/blog/businesses/65/articles").json()["articles"] == []
            assert client.get("/public/blog/businesses/65/articles/kak-vesti-zapis-klientov").status_code == 404
            assert client.get("/public/blog/businesses/66/articles").status_code == 404
            assert client.get("/public/blog/businesses/0/articles").status_code == 404
    finally:
        app.dependency_overrides.clear()
