import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.competitive_intelligence.contracts import RunInput
from app.competitive_intelligence.evidence_store import normalize
from app.competitive_intelligence.service import IntelligenceService
from app.competitive_intelligence.worker import run_intelligence_once
from app.core.composition import build_intelligence
from app.core.config import Settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.schemas import LLMResponse, LLMUsage
from app.main import app
from app.models.domain import AgentRun, Artifact, CostRecord, Product, Task, TaskStatus
from app.workflows.research_contracts import ResearchSourceBundle


def source(index=1, **changes):
    return {"url": f"https://reviews.example/{index}", "title": f"Review {index}",
            "competitor": "Competitor A", "platform": "Reviews", "excerpt": f"Support response is slow. Item {index}",
            "published_at": "2026-10-01T10:00:00Z", **changes}


def bundle(*sources):
    return ResearchSourceBundle.model_validate({"label": "Public sources", "sources": list(sources)})


class ResearchProvider:
    def __init__(self, *, confidence=.95, bad_ids=False, fail_stage=None, mutate=None):
        self.requests = []
        self.confidence = confidence
        self.bad_ids = bad_ids
        self.fail_stage = fail_stage
        self.mutate = mutate

    async def generate(self, request):
        self.requests.append(request)
        stage = request.metadata["task_type"]
        payload = json.loads(request.messages[0].content)
        if stage == self.fail_stage:
            raise RuntimeError("password=must-not-leak")
        if stage == "planning":
            data = {"mode": payload["request"]["mode"], "questions": ["What should be tested?"],
                    "source_requests": ["Add dated competitor reviews and tariff excerpts."]}
        elif "triage" in stage:
            data = {"labels": [{"evidence_id": "invented" if self.bad_ids else item["evidence_id"],
                "relevance": "relevant", "topic": "price" if item["source_type"] == "pricing" else "support",
                "sentiment": "positive" if "excellent" in item["excerpt"] else "negative",
                "confidence": self.confidence} for item in payload["items"]]}
        else:
            evidence = payload["evidence"]
            row = evidence[0]
            data = {"insights": [{"view": "pricing" if row["source_type"] == "pricing" else "weaknesses",
                "statement": "В источнике отмечен сигнал для проверки.", "impact": "medium", "confidence": .99,
                "state": "signal", "evidence_ids": [row["id"]], "quotes": [row["excerpt"]],
                "suggested_test": "Проверить проблему в интервью с клиентами."},
                {"view": "opportunities", "statement": "Проверить предложение с быстрой поддержкой.",
                 "impact": "high", "confidence": .9, "state": "signal", "evidence_ids": [row["id"]],
                 "quotes": [], "suggested_test": "Провести интервью."}], "evidence_gaps": []}
            if self.mutate:
                self.mutate(data, evidence)
        return LLMResponse(content=json.dumps(data), model="fake", stop_reason="end_turn",
                           usage=LLMUsage(input_tokens=10, output_tokens=20))


@pytest.fixture
def product(db):
    row = Product(name="Research company", slug="research-company")
    db.add(row)
    db.commit()
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,kind", [("reviews", "review"), ("weaknesses", "review"), ("pricing", "pricing")])
async def test_durable_lifecycle_modes_evidence_and_export(db, product, tmp_path, mode, kind):
    strong, jeff = ResearchProvider(), ResearchProvider()
    service = build_intelligence(db, Settings(products_root=tmp_path), strong, jeff)
    state = service.start(RunInput(product_id=product.id, mode=mode, brief="Research competitors",
        source_bundle=bundle(source(source_type=kind, excerpt="Тариф 100 рублей" if kind == "pricing" else "Медленная поддержка"))))
    run_id = UUID(state["id"])
    assert state["status"] == "queued"
    assert await service.execute(run_id)
    persisted = IntelligenceService(db)
    state = persisted.get(run_id)
    assert state["status"] == "completed"
    assert [event["stage"] for event in state["events"]] == ["planning", "sources", "triage", "analysis", "validation", "ready"]
    assert state["counts"] == {"found": 1, "retained": 1, "excluded": 0, "escalated": 0, "jeff_processed": 1}
    evidence = persisted.evidence(run_id)
    assert evidence[0]["validated_by"] == "jeff"
    report = persisted.report(run_id)
    assert report["insights"][0]["confidence"] == .95
    assert report["insights"][0]["evidence_count"] == report["insights"][0]["source_count"] == 1
    assert report["insights"][1]["state"] == "inference"
    assert len(jeff.requests) == 1 and len(str(jeff.requests[0].messages)) < 3000
    assert len(strong.requests) == 2
    assert len(list(db.scalars(select(CostRecord)))) == 3
    assert all(run.status == "completed" for run in db.scalars(select(AgentRun)))
    assert not await service.execute(run_id)
    exported = persisted.export(run_id, "i1", "content_hypothesis")
    assert exported == persisted.export(run_id, "i1", "content_hypothesis")
    task = db.get(Task, UUID(exported["task_id"]))
    assert task.product_id == product.id and task.parent_id == run_id
    assert evidence[0]["source_url"] in task.description
    artifact = db.get(Artifact, UUID(exported["artifact_id"]))
    assert artifact.kind == "content_hypothesis"
    assert state["report_artifact_id"] in artifact.content


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["absent", "low_confidence", "bad_ids", "unavailable"])
async def test_jeff_escalates_to_strong_without_forged_provenance(db, product, tmp_path, failure):
    jeff = None if failure == "absent" else ResearchProvider(confidence=.2 if failure == "low_confidence" else .95,
        bad_ids=failure == "bad_ids", fail_stage="jeff_triage" if failure == "unavailable" else None)
    strong = ResearchProvider()
    service = build_intelligence(db, Settings(products_root=tmp_path), strong, jeff)
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Check competitors", source_bundle=bundle(source())))['id'])
    await service.execute(run_id)
    assert service.get(run_id)["counts"]["escalated"] == 1
    row = service.evidence(run_id)[0]
    assert row["validated_by"] == "strong_llm"
    assert row["jeff_confidence"] == (.2 if failure == "low_confidence" else None)
    assert [r.metadata["task_type"] for r in strong.requests] == ["planning", "strong_triage", "synthesis"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail,confidence", [("strong_triage", .95), (None, .1)])
async def test_uncertain_strong_labels_fail_closed(db, product, tmp_path, fail, confidence):
    strong = ResearchProvider(fail_stage=fail, confidence=confidence)
    service = build_intelligence(db, Settings(products_root=tmp_path), strong)
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Review evidence", source_bundle=bundle(source())))['id'])
    await service.execute(run_id)
    assert service.report(run_id)["status"] == "needs_sources"
    assert service.report(run_id)["insights"] == []
    assert service.evidence(run_id)[0]["exclusion_reason"] in {"low_confidence", "triage_unavailable"}


def test_deduplication_publication_period_and_recency_are_rules(product):
    request = RunInput(product_id=product.id, brief="Review evidence", date_from="2026-10-01", date_to="2026-10-07",
                       competitors=["Competitor A"])
    rows = normalize([bundle(source(), source(2, excerpt=source()["excerpt"]), source(3, published_at=None),
        source(4, published_at="2020-01-01T00:00:00Z"), source(5, competitor="Other"),
        source(6, published_at="2027-01-01T00:00:00Z"))], request, datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert [row.exclusion_reason for row in rows] == [None, "duplicate", "unknown_publication_date", "outside_period",
                                                    "competitor_filter", "future_publication_date"]
    assert rows[2].published_at is None and rows[2].recency_score is None
    request.date_from = request.date_to = None
    old = normalize([bundle(source(published_at="2020-01-01T00:00:00Z"))], request, datetime(2026, 10, 7, tzinfo=timezone.utc))[0]
    assert old.exclusion_reason is None and old.recency_score < .1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["citation", "quote", "numeric", "pricing"])
async def test_validator_rejects_invented_external_facts(db, product, tmp_path, bad):
    def mutate(data, evidence):
        data["insights"] = data["insights"][:1]
        row = data["insights"][0]
        if bad == "citation": row["evidence_ids"] = ["made_up"]
        if bad == "quote": row["quotes"] = ["This is a fabricated quote"]
        if bad == "numeric": row["statement"] = "Тариф стоит 999999 рублей"
        if bad == "pricing": row["view"] = "pricing"
    strong = ResearchProvider(mutate=mutate)
    service = build_intelligence(db, Settings(products_root=tmp_path), strong)
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Check sources", source_bundle=bundle(source())))['id'])
    await service.execute(run_id)
    assert service.report(run_id)["insights"] == []
    assert service.report(run_id)["status"] == "partial"
    assert "отклонено" in " ".join(service.report(run_id)["evidence_gaps"])


@pytest.mark.asyncio
async def test_conflicting_observations_are_preserved(db, product, tmp_path):
    def mutate(data, evidence):
        data["insights"][0]["evidence_ids"] = [item["id"] for item in evidence]
    service = build_intelligence(db, Settings(products_root=tmp_path), ResearchProvider(mutate=mutate))
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Compare reviews",
        source_bundle=bundle(source(), source(2, excerpt="Support is excellent"))))['id'])
    await service.execute(run_id)
    assert service.report(run_id)["insights"][0]["state"] == "conflict"
    assert service.report(run_id)["insights"][0]["evidence_count"] == 2


@pytest.mark.asyncio
async def test_source_snapshot_followup_and_depth_are_bounded(db, product, tmp_path):
    service = build_intelligence(db, Settings(products_root=tmp_path), ResearchProvider())
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Check sources", depth="quick",
        source_bundle=bundle(*(source(i) for i in range(12)))))['id'])
    other = bundle(source(99))
    db.add(Artifact(product_id=product.id, kind="research_source_bundle", title=other.label, content=other.model_dump_json()))
    db.commit()
    await service.execute(run_id)
    assert service.get(run_id)["counts"]["found"] == 12
    assert service.get(run_id)["counts"]["retained"] == 10
    assert sum(row["exclusion_reason"] == "depth_limit" for row in service.evidence(run_id)) == 2
    follow = service.follow_up(run_id, "Research a follow-up")
    await service.execute(UUID(follow["id"]))
    assert service.get(UUID(follow["id"]))["counts"]["found"] == 12
    assert service.report(run_id)["insights"]


@pytest.mark.asyncio
async def test_without_sources_makes_no_findings_and_keeps_empty_followup(db, product, tmp_path):
    provider = ResearchProvider()
    service = build_intelligence(db, Settings(products_root=tmp_path), provider)
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Check sources"))['id'])
    await service.execute(run_id)
    assert service.report(run_id)["status"] == "needs_sources"
    assert service.report(run_id)["insights"] == []
    assert len(provider.requests) == 1
    new = bundle(source())
    db.add(Artifact(product_id=product.id, kind="research_source_bundle", title=new.label, content=new.model_dump_json()))
    db.commit()
    follow = UUID(service.follow_up(run_id, "Check same evidence again")["id"])
    await service.execute(follow)
    assert service.evidence(follow) == []


@pytest.mark.asyncio
async def test_privacy_and_failure_state_are_durable(db, product, tmp_path):
    provider = ResearchProvider(fail_stage="synthesis")
    service = build_intelligence(db, Settings(products_root=tmp_path), provider)
    run_id = UUID(service.start(RunInput(product_id=product.id, brief="Check sk-secret123456 evidence",
        source_bundle=bundle(source(excerpt="Authorization: Bearer do-not-include"))))['id'])
    await service.execute(run_id)
    assert service.get(run_id)["status"] == "failed"
    assert service.get(run_id)["evidence_artifact_id"]
    all_requests = " ".join(request.model_dump_json() for request in provider.requests)
    all_artifacts = " ".join(row.content for row in db.scalars(select(Artifact)))
    assert "do-not-include" not in all_requests + all_artifacts
    assert "sk-secret123456" not in all_requests + all_artifacts
    assert "must-not-leak" not in service.get(run_id)["error"]
    assert db.get(Task, run_id).status == TaskStatus.FAILED


@pytest.fixture
def api_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        product = Product(name="Company <script>alert(1)</script>", slug="demo-company")
        db.add(product)
        db.commit()
        app.dependency_overrides[get_db] = lambda: db
        try:
            yield db, factory, product
        finally:
            app.dependency_overrides.clear()
    engine.dispose()


@pytest.mark.asyncio
async def test_api_queue_worker_progress_sources_export_and_page(api_db, tmp_path):
    db, factory, product = api_db
    with TestClient(app) as client:
        response = client.post('/competitive-intelligence/runs', json={"product_id": str(product.id),
            "brief": "Investigate support", "source_bundle": bundle(source()).model_dump(mode="json")})
        assert response.status_code == 202
        run_id = response.json()["id"]
        assert client.get(f'/competitive-intelligence/runs/{run_id}/report').status_code == 409
        assert await run_intelligence_once(factory, Settings(products_root=tmp_path),
            builder=lambda db, settings: build_intelligence(db, settings, ResearchProvider(), ResearchProvider())) == 1
        db.expire_all()
        assert client.get(f'/competitive-intelligence/runs/{run_id}').json()["status"] == "completed"
        events = client.get(f'/competitive-intelligence/runs/{run_id}/events', headers={"Last-Event-ID": "4"})
        assert events.status_code == 200
        assert 'id: 5' in events.text and 'id: 1\n' not in events.text and 'event: done' in events.text
        assert len(client.get(f'/competitive-intelligence/runs/{run_id}/evidence?disposition=retained').json()) == 1
        assert client.get(f'/competitive-intelligence/runs/{run_id}/evidence?disposition=excluded').json() == []
        assert client.get(f'/competitive-intelligence/runs/{run_id}/evidence?disposition=madeup').status_code == 422
        export = client.post(f'/competitive-intelligence/runs/{run_id}/export', json={"insight_id": "i1", "target": "task"})
        assert export.status_code == 201
        assert client.post(f'/competitive-intelligence/runs/{run_id}/follow-up', json={"brief": "Compare more carefully"}).status_code == 202
        page = client.get(f'/intelligence?product_id={product.id}')
        assert page.status_code == 200 and 'Бизнес-разведка' in page.text
        assert '<script>alert(1)</script>' not in page.text
        assert 'Компания' in page.text and 'Карта слабостей' in page.text and 'Возможности' in page.text
        assert client.get('/intelligence/assets/page.js').status_code == 200
        assert client.get(f'/competitive-intelligence/runs/{uuid4()}').status_code == 404
        assert client.post('/competitive-intelligence/runs', json={"product_id": str(uuid4()), "brief": "Test research"}).status_code == 404
        assert client.post('/competitive-intelligence/runs', json={"product_id": str(product.id), "brief": "Test research",
            "date_from": "2026-10-07", "date_to": "2026-10-01"}).status_code == 422


@pytest.mark.asyncio
async def test_worker_recovers_interrupted_run_and_unconfigured_models(api_db, tmp_path):
    db, factory, product = api_db
    service = IntelligenceService(db)
    stale_id = UUID(service.start(RunInput(product_id=product.id, brief="Interrupted research"))["id"])
    task = db.get(Task, stale_id)
    task.status = TaskStatus.RUNNING
    task.updated_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    db.commit()
    def unconfigured(db, settings):
        raise ValueError('Missing credentials')
    assert await run_intelligence_once(factory, Settings(products_root=tmp_path), builder=unconfigured) == 0
    db.expire_all()
    assert service.get(stale_id)["error"] == "research_interrupted"
    queued_id = UUID(service.start(RunInput(product_id=product.id, brief="Unconfigured model"))["id"])
    assert await run_intelligence_once(factory, Settings(products_root=tmp_path), builder=unconfigured) == 1
    db.expire_all()
    assert service.get(queued_id)["error"] == "research_model_unconfigured"
    assert db.get(Task, queued_id).status == TaskStatus.FAILED
