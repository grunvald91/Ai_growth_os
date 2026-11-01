"""Offline regression tests for science gate and company-scoped topic picker."""
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.content.whiteboard.contracts import WhiteboardRequest
from app.content.whiteboard.jobs import required_calls
from app.content.whiteboard.science import (
    FactFinding, FactReview, evidence_supported, reference_anchors, requires_science_review,
)
from app.content.whiteboard.topics import suggest_company_video_topics
from app.models.domain import Product
from test_whiteboard_jobs import environment, client  # noqa: F401


def science_request(**changes):
    values = {"product_id": uuid4(), "topic": "Почему горячее тело массивнее холодного?",
              "science_mode": "auto"}
    values.update(changes)
    return WhiteboardRequest(**values)


def test_explicit_scientific_classification_and_legacy_serializer():
    req = science_request()
    assert requires_science_review(req)
    assert required_calls(req) == 15
    assert required_calls(req, visual_review=True) == 20
    assert not requires_science_review(req.model_copy(update={"science_mode": "general"}))
    legacy = science_request(science_mode="general")
    assert "science_mode" not in legacy.model_dump()
    assert "source_script" not in legacy.model_dump()
    assert "reference_materials" not in legacy.model_dump()
    enhanced = science_request(source_script="Первоначальный сценарий автора.",
                               background_notes="Контекст аудитории")
    assert WhiteboardRequest.model_validate_json(enhanced.model_dump_json()) == enhanced


def test_hot_mass_reference_is_grounded_and_unsupported_claim_blocks():
    req = science_request()
    assert [r["id"] for r in reference_anchors(req)] == ["SEP", "NIST"]
    approved = FactReview(status="supported", summary="Подтверждено заявленными источниками.",
        verified_source_ids=["SEP", "NIST"],
        findings=[FactFinding(claim="Нагретое тело получает дополнительную энергию.",
                              verdict="supported", explanation="Дополнительная энергия меняет массу.",
                              reference_id="SEP")])
    assert evidence_supported(approved, req)
    contradicted = FactReview(status="blocked", summary="Обнаружена научная ошибка в тексте.",
        findings=[FactFinding(claim="Масса тела при нагреве совсем не изменяется.",
                              verdict="contradicted", explanation="Энергия и масса эквивалентны.",
                              reference_id="SEP")])
    assert not evidence_supported(contradicted, req)
    assert not evidence_supported(approved, science_request(topic="Почему электроны взаимодействуют?",
                                                             science_mode="scientific"))


def _write_profile(settings, slug):
    location = settings.products_root / slug
    location.mkdir(parents=True, exist_ok=True)
    (location / "company-profile.json").write_text(json.dumps({
        "schema_version": "1.0",
        "identity": {"name": "Салон", "category": "салон красоты"},
        "offer": {"products_services": [{"name": "стрижка", "description": "Стрижка волос"}]},
        "customers": {"pains": ["как выбрать удобное время"]},
    }, ensure_ascii=False), encoding="utf-8")


def test_suggestions_are_scoped_to_validated_company_profiles(environment):
    _, settings, company_id, other_id = environment
    _write_profile(settings, "whiteboard-jobs")
    own = suggest_company_video_topics(settings.products_root, Product(name="Салон", slug="whiteboard-jobs"))
    other = suggest_company_video_topics(settings.products_root, Product(name="Другое", slug="other-jobs"))
    assert own["profile_ready"] and len(own["topics"]) >= 3
    assert any("стрижка" in item["topic"] for item in own["topics"])
    assert not other["profile_ready"] and not other["topics"]


def test_topic_picker_and_quote_are_authenticated_and_company_scoped(environment):
    _, settings, product_id, other_id = environment
    _write_profile(settings, "whiteboard-jobs")
    browser = client()
    response = browser.get(f"/studio/products/{product_id}/whiteboard/topics.json")
    assert response.status_code == 200
    proposal = response.json()["topics"][0]["topic"]
    quote = browser.get(f"/studio/products/{product_id}/whiteboard/quote.json",
                        params={"topic": proposal, "duration_seconds": 30,
                                "science_mode": "general", "visual_review": "once",
                                "max_api_calls": "auto"})
    assert quote.status_code == 200
    assert quote.json()["limit"] == 19
    assert browser.get(f"/studio/products/{other_id}/whiteboard/topics.json").json()["topics"] == []
    html = browser.get(f"/studio/products/{product_id}/whiteboard").text
    assert 'id="wb-suggest-btn"' in html and 'name="source_script"' in html
    assert 'id="wb-topic-dialog"' in html


def test_picker_paid_start_rejects_unlisted_topic_without_creating_job(environment):
    _, settings, product_id, _ = environment
    _write_profile(settings, "whiteboard-jobs")
    browser = client()
    response = browser.post(f"/studio/products/{product_id}/whiteboard", data={
        "topic": "Тема не из профиля компании",
        "duration_seconds": "30", "science_mode": "general",
        "wb_paid_start": "yes", "wb_paid_consent": "yes",
        "wb_topic_choice": "Тема не из профиля компании",
    }, follow_redirects=False)
    assert response.status_code == 409
    from app.db.session import get_db
    # The response is rejected by the server before any job is queued.


@pytest.mark.asyncio
async def test_science_disagreement_stops_before_images_and_voice(tmp_path):
    from app.content.whiteboard.pipeline import WhiteboardPipeline
    from app.content.whiteboard.runtime import ReadinessError
    from app.privacy import PrivacyGateway
    from test_whiteboard import FakePlanner, FakeRenderer, FakeRegions, FakeSpeech

    calls = []
    class Reviewer:
        async def review(self, storyboard, request):
            calls.append("review")
            return FactReview(
                status="blocked", summary="Не подтверждено утверждение о постоянной массе.",
                verified_source_ids=["SEP"],
                findings=[FactFinding(
                    claim="При нагревании масса остаётся точно такой же.",
                    verdict="contradicted",
                    explanation="Энергия нагрева увеличивает массу тела.",
                    reference_id="SEP",
                )])
    class NoImages:
        async def generate(self, scene, request):
            pytest.fail("Image generation must not start before science review")
    class NoVoice:
        async def synthesize(self, *args, **kwargs):
            pytest.fail("Speech generation must not start before science review")

    out = tmp_path / "video.mp4"
    pipeline = WhiteboardPipeline(storyboard=FakePlanner(), science_reviewer=Reviewer(),
        images=NoImages(), speech=NoVoice(), regions=FakeRegions(), renderer=FakeRenderer(),
        sanitizer=PrivacyGateway())
    with pytest.raises(ReadinessError, match="science_unsupported"):
        await pipeline.produce(science_request(science_mode="scientific", request_generation=True),
                               out, approval_verified=True)
    assert calls == ["review"] and not out.exists()


@pytest.mark.asyncio
async def test_picker_explicit_consent_starts_existing_queue_path(environment, monkeypatch):
    from app.api import whiteboard as api
    from sqlalchemy import select
    from app.models.domain import Task, TaskStatus

    async def ready(*args, **kwargs):
        return None
    monkeypatch.setattr(api, "preflight", ready)
    factory, settings, product_id, _ = environment
    _write_profile(settings, "whiteboard-jobs")
    browser = client()
    topic = browser.get(f"/studio/products/{product_id}/whiteboard/topics.json").json()["topics"][0]["topic"]
    response = browser.post(f"/studio/products/{product_id}/whiteboard", data={
        "topic": topic, "duration_seconds": "30", "science_mode": "general",
        "profile": "gemini_ru", "visual_review": "once", "max_api_calls": "auto",
        "wb_paid_start": "yes", "wb_paid_consent": "yes", "wb_topic_choice": topic,
    }, follow_redirects=False)
    assert response.status_code == 303
    with factory() as db:
        jobs = list(db.scalars(select(Task).where(Task.product_id == product_id,
                         Task.assigned_agent == "whiteboard_renderer")))
        assert len(jobs) == 1 and jobs[0].status == TaskStatus.QUEUED
