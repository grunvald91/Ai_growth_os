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


def test_science_review_requires_complete_beat_coverage():
    from test_whiteboard import storyboard
    req = science_request()
    story = storyboard()
    # A single legitimate citation cannot attest all six narration beats.
    one = FactReview(status="supported", summary="Неполная научная проверка.",
        verified_source_ids=["SEP"],
        findings=[FactFinding(claim=story.scenes[0].beats[0].narration,
                              verdict="supported", explanation="Проверено по источнику.",
                              reference_id="SEP")])
    assert not evidence_supported(one, req, story)
    # A complete review, preserving beat identity, can pass.
    narrations = [beat.narration for scene in story.scenes for beat in scene.beats][:-1]
    complete = FactReview(status="supported", summary="Все реплики проверены по источнику.",
        verified_source_ids=["SEP"],
        findings=[FactFinding(claim=line, verdict="supported",
                              explanation="Утверждение подтверждено источником.",
                              reference_id="SEP") for line in narrations])
    assert evidence_supported(complete, req, story)
    assert not evidence_supported(complete.model_copy(update={
        "findings": [*complete.findings, complete.findings[0]]
    }), req, story)


def test_automatic_science_review_detects_supplied_script_and_quote(environment):
    from app.content.whiteboard.science import requires_science_review
    _, settings, product_id, _ = environment
    req = WhiteboardRequest(product_id=product_id, topic="Короткое объяснение для зрителей",
        science_mode="auto", source_script="Энергия нагрева увеличивает массу тела.")
    assert requires_science_review(req)
    browser = client()
    quote = browser.post(f"/studio/products/{product_id}/whiteboard/quote.json",
        data={"topic": req.topic, "science_mode": "auto",
              "source_script": req.source_script,
              "duration_seconds": "30", "visual_review": "off",
              "max_api_calls": "auto"})
    assert quote.status_code == 200
    assert quote.json()["science_review"] is True
    assert quote.json()["required_calls"] == 15


def test_model_science_schema_has_no_server_only_fields_and_is_strict():
    from app.content.whiteboard.science import science_response_schema
    schema = science_response_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False
    assert "source_links" not in schema["properties"]
    assert "verified_source_ids" not in schema["properties"]
    finding = schema["properties"]["findings"]["items"]
    assert finding["additionalProperties"] is False
    assert set(finding["required"]) == set(finding["properties"])


def test_science_v1_enforced_even_if_general_selected_and_legacy_remains_unchanged():
    request = science_request(science_mode="general", science_policy_version=1)
    assert requires_science_review(request)
    assert required_calls(request) == 15
    legacy = request.model_copy(update={"science_policy_version": 0})
    assert not requires_science_review(legacy)
    assert "science_policy_version" not in legacy.model_dump()
    assert WhiteboardRequest.model_validate_json(request.model_dump_json()).science_policy_version == 1


def test_scientific_general_quote_does_not_bypass_review(environment):
    _, settings, product_id, _ = environment
    browser = client()
    quote = browser.post(f"/studio/products/{product_id}/whiteboard/quote.json", data={
        "topic": "Почему заряженный аккумулятор обладает большей массой?",
        "duration_seconds": "30", "science_mode": "general",
        "max_api_calls": "auto", "visual_review": "off",
    })
    assert quote.status_code == 200
    assert quote.json()["science_review"] is True
    assert quote.json()["required_calls"] == 15


@pytest.mark.asyncio
async def test_crossref_discovery_fetches_actual_abstract_and_uses_doi_not_snippet():
    import httpx
    from app.content.whiteboard.science import retrieved_references
    hits = []
    def transport(req):
        hits.append((req.method, req.url.host))
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": [{
                "type": "journal-article",
                "DOI": "10.1234/quantum-physics",
                "title": ["Quantum electron interactions and electromagnetic energy"],
                "abstract": "<jats:p>Electron interactions produce electromagnetic forces. "
                            "The published experiment compares the measured effects with "
                            "theoretical predictions under reproducible conditions and "
                            "reports a consistent interaction in the observed samples.</jats:p>",
            }]}})
        if req.url.host == "html.duckduckgo.com":
            return httpx.Response(200, text="")
        pytest.fail("Unapproved network target: " + str(req.url))
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        result = await retrieved_references(science_request(
            topic="Как электроны взаимодействуют друг с другом?", science_mode="scientific"
        ), client=client)
    assert len(result) == 1
    assert result[0]["id"] == "CR1"
    assert result[0]["url"] == "https://doi.org/10.1234/quantum-physics"
    assert "reproducible conditions" in result[0]["claim"]
    assert hits[0] == ("GET", "api.crossref.org")


@pytest.mark.asyncio
async def test_science_search_rejects_untrusted_urls_without_contacting_them():
    import httpx
    from app.content.whiteboard.science import retrieved_references
    calls = []
    def transport(req):
        calls.append(req.url.host)
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message": {"items": []}})
        if req.url.host == "html.duckduckgo.com":
            return httpx.Response(200, text="")
        pytest.fail("Unapproved outbound host: " + str(req.url))
    request = science_request(
        topic="Как работают квантовые взаимодействия?",
        reference_materials="https://nist.gov.evil.example/private https://127.0.0.1/admin"
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        assert await retrieved_references(request, client=client) == []
    assert all(host in {"api.crossref.org", "html.duckduckgo.com"} for host in calls)


@pytest.mark.asyncio
async def test_no_public_science_evidence_blocks_before_first_paid_call(tmp_path, monkeypatch):
    from app.content.whiteboard import science
    from app.content.whiteboard.pipeline import WhiteboardPipeline
    from app.content.whiteboard.runtime import ReadinessError
    from app.privacy import PrivacyGateway
    from test_whiteboard import FakePlanner, FakeImages, FakeSpeech, FakeRegions, FakeRenderer
    from types import SimpleNamespace

    async def no_evidence(*args, **kwargs):
        return []
    monkeypatch.setattr(science, "retrieved_references", no_evidence)
    class NoService:
        async def generate(self, _):
            pytest.fail("Paid scientific call must not start without references")
    reviewer = science.FactReviewer(NoService(), "gemini-flash-3.8")
    planner = FakePlanner()
    pipeline = WhiteboardPipeline(storyboard=planner, science_reviewer=reviewer,
        images=FakeImages(), speech=FakeSpeech(), regions=FakeRegions(),
        renderer=FakeRenderer(), sanitizer=PrivacyGateway())
    with pytest.raises(ReadinessError, match="science_sources_missing"):
        await pipeline.produce(science_request(
            topic="Как взаимодействуют электронные облака?", science_mode="scientific",
            request_generation=True), tmp_path / "video.mp4", approval_verified=True)
    assert planner.calls == 0


@pytest.mark.asyncio
async def test_science_quote_must_match_retrieved_publication_text():
    from app.content.whiteboard.science import FactReviewer
    from test_whiteboard import storyboard
    from types import SimpleNamespace

    request = science_request(topic="Почему электрон получает энергию?", science_mode="scientific")
    scene = storyboard()
    narration = [b.narration for s in scene.scenes for b in s.beats][:-1]
    sources = [{
        "id": "CR1", "url": "https://doi.org/10.1234/credible-paper",
        "claim": "Research reports an increased transfer of energy in the observed process."
    }]
    class Provider:
        async def generate(self, query):
            return SimpleNamespace(content=json.dumps({
                "status":"supported", "summary":"Все высказывания подтверждены опубликованным материалом.",
                "findings":[{
                    "claim": line, "verdict":"supported",
                    "explanation":"В тексте научного источника приведено соответствующее утверждение.",
                    "reference_id":"CR1", "evidence_quote":"This quote is not in the source",
                } for line in narration]
            }, ensure_ascii=False))
    reviewer = FactReviewer(Provider(), "gemini-flash-3.8")
    reviewer._sources = sources
    result = await reviewer.review(scene, request)
    assert result.status == "blocked"
    assert not evidence_supported(result, request, scene)


def test_topics_are_diverse_and_business_grounded(environment):
    from app.company_profile import parse_company_profile
    from app.content.whiteboard.topics import suggest_company_video_topics
    _, settings, product_id, _ = environment
    _write_profile(settings, "whiteboard-jobs")
    profile = settings.products_root / "whiteboard-jobs" / "company-profile.json"
    data = json.loads(profile.read_text(encoding="utf-8"))
    data["customers"]["jobs_to_be_done"] = ["Выбрать удобную запись"]
    data["customers"]["pains"] = ["Клиент не понимает доступное время",
                                   "Возникают вопросы перед визитом"]
    profile.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    own = suggest_company_video_topics(settings.products_root, Product(
        name="Салон", slug="whiteboard-jobs"))
    titles = [item["topic"] for item in own["topics"]]
    assert len(titles) >= 5
    assert len(set(titles)) == len(titles)
    assert any("стрижка" in title for title in titles)
    assert any("Клиент не понимает" in title for title in titles)
    assert any("салон" in title for title in titles)
    assert not any("гарантируем" in title.lower() for title in titles)


@pytest.mark.asyncio
async def test_crossref_discards_unrelated_abstract_even_with_real_doi():
    import httpx
    from app.content.whiteboard.science import retrieved_references
    def handler(req):
        if req.url.host == "api.crossref.org":
            return httpx.Response(200, json={"message":{"items":[{
                "DOI":"10.1987/unrelated", "type":"journal-article",
                "title":["A clinical discussion of gardening and ancient agriculture"],
                "abstract":"This unrelated research abstract discusses gardening, soil, "
                            "crop production and ancient agriculture. It provides "
                            "historical evidence about agriculture in several "
                            "different cultures and periods, not quantum mechanics.",
            }]}})
        return httpx.Response(200, text="")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        refs = await retrieved_references(science_request(
            topic="Почему электроны двигаются и взаимодействуют?", science_mode="scientific"),
            client=client)
    assert not refs


@pytest.mark.asyncio
async def test_cached_science_sources_survive_resume_without_network_lookup(tmp_path, monkeypatch):
    from app.content.whiteboard import science
    from app.content.whiteboard.checkpoints import CheckpointStore
    count = 0

    async def discovery(request):
        nonlocal count
        count += 1
        return [{"id":"CR1", "url":"https://doi.org/10.9999/record",
                 "claim":"The scientific abstract discusses electron energy and motion."}]
    monkeypatch.setattr(science, "retrieved_references", discovery)
    req = science_request(topic="Почему движутся электроны?", science_policy_version=1,
                          request_generation=True)
    stage = CheckpointStore(tmp_path/"test-job", req,
        models={"storyboard":"test", "image":"test", "speech":"test", "regions":"test"})
    class NoCalls:
        async def generate(self, request):
            pytest.fail("Evidence lookup must never make model calls")
    first = science.FactReviewer(NoCalls(), "test")
    await first.prepare_sources(req, checkpoints=stage)
    second = science.FactReviewer(NoCalls(), "test")
    await second.prepare_sources(req, checkpoints=stage)
    assert count == 1
    assert second._sources == first._sources


@pytest.mark.asyncio
async def test_scientific_visual_repair_imports_factcheck_and_never_rebuys_it(tmp_path, monkeypatch):
    from app.content.whiteboard.checkpoints import CheckpointStore
    from app.content.whiteboard.science import FactReview, FactFinding, ScienceSources, FactReviewer
    from app.content.whiteboard.repair import reuse_verified_science
    from app.content.whiteboard import science
    from test_whiteboard import storyboard

    req = science_request(science_policy_version=1, request_generation=True)
    story = storyboard()
    model_ids = {"storyboard":"test-author", "image":"test-image",
                 "speech":"test-speech", "regions":"test-critic"}
    original = CheckpointStore(tmp_path/"original", req, models=model_ids)
    destination = CheckpointStore(tmp_path/"visual-repair", req, models=model_ids, visual_review=True)
    sources = ScienceSources(sources=[{
        "id":"SEP", "url":"https://plato.stanford.edu/entries/equivME/",
        "claim":"For a closed system, receiving energy raises its rest mass."
    }])
    all_lines = [b.narration for s in story.scenes for b in s.beats][:-1]
    assessment = FactReview(status="supported",
        summary="Все шесть реплик проверены по физическим источникам.",
        verified_source_ids=["SEP"],
        source_links=[{"id":"SEP","url":"https://plato.stanford.edu/entries/equivME/"}],
        findings=[FactFinding(claim=line, verdict="supported",
            explanation="Энергетический баланс подтверждает формулировку.",
            reference_id="SEP") for line in all_lines])
    async def source():
        return sources
    async def review():
        return assessment
    await original.json_stage("science-sources", source, ScienceSources)
    await original.json_stage("science-review", review, FactReview)
    from uuid import uuid4
    await reuse_verified_science(original, destination, request=req, storyboard=story,
                                 source_job_id=uuid4(), source_model_id="gemini-flash-3.8")
    async def no_web(*args, **kwargs):
        pytest.fail("Scientific visual repair must reuse checkpointed evidence")
    monkeypatch.setattr(science, "retrieved_references", no_web)
    class NoLLM:
        async def generate(self, request):
            pytest.fail("Scientific visual repair must reuse checkpointed LLM verdict")
    new_reviewer = FactReviewer(NoLLM(), "gemini-flash-3.8")
    await new_reviewer.prepare_sources(req, checkpoints=destination)
    assert new_reviewer._sources == sources.sources
    async def must_not_run():
        pytest.fail("No second paid scientific call")
    result = await destination.json_stage("science-review", must_not_run, FactReview)
    assert result.model_dump() == assessment.model_dump()
