"""Programmatic SEO must suggest pages without fabricating offers, city coverage or traffic."""
from datetime import datetime, timezone

import pytest

from app.api.seo_plan_board import seo_plan_board
from app.api.seo_run_view import seo_run_summary
from app.company_profile import CompanyProfile
from app.models.domain import Artifact, Product, Task, TaskStatus
from app.seo.activation import set_enabled
from app.seo.contracts import QueryCluster, SemanticCore, SourceStatus
from app.seo.landing import (
    KIND, LandingOpportunities, make_landing_opportunities, store_landing_opportunities,
)


def _cluster(number, query, intent):
    return QueryCluster(
        id=f"c{number:03d}", head_query=query, queries=[query],
        intent=intent, stage="decision" if intent == "transactional" else "consideration",
        engines=["yandex"],
    )


def _core(*clusters):
    return SemanticCore(
        seeds=["онлайн запись"], collected_at=datetime.now(timezone.utc),
        sources=[SourceStatus(engine="yandex", requests=1, failed_requests=0, queries_found=len(clusters))],
        clusters=list(clusters),
    )


def _profile():
    return CompanyProfile.model_validate({
        "schema_version": "1.0",
        "identity": {"name": "Studio", "service_geography": ["Сочи"]},
        "offer": {"products_services": [
            {"name": "Онлайн-запись", "description": "Подтверждённая функция записи",
             "release_status": "live", "evidence_refs": ["verified-feature"]},
            {"name": "Smart DNS", "description": "DNS for devices",
             "release_status": "limited", "evidence_refs": ["verified-device"]},
            {"name": "Брус", "description": "Продукт ещё не продаётся",
             "release_status": "in_development"},
        ]},
    })


def test_commercial_search_opportunities_are_drafts_not_live_pages():
    plan = make_landing_opportunities(_core(
        _cluster(1, "онлайн запись цена", "commercial"),
        _cluster(2, "купить онлайн запись в Сочи", "transactional"),
        _cluster(3, "smart dns для iphone цена", "commercial"),
        _cluster(4, "купить брус", "transactional"),
    ), _profile())
    assert plan.mode == "draft_only" and plan.demand_data == "none"
    assert len(plan.candidates) == 4
    by_query = {item.primary_query: item for item in plan.candidates}
    normal = by_query["онлайн запись цена"]
    assert normal.status == "review"
    assert normal.verified_offer and normal.offer_name == "Онлайн-запись"
    assert normal.proposed_path.startswith("/solutions/")

    local = by_query["купить онлайн запись в Сочи"]
    assert local.page_type == "location" and local.geography == "Сочи"
    assert local.status == "blocked" and local.reason_code == "local_value_required"

    device = by_query["smart dns для iphone цена"]
    assert device.page_type == "device" and device.status == "review"
    assert by_query["купить брус"].reason_code == "offer_evidence_required"


def test_short_verified_product_names_match_the_actual_query():
    profile = CompanyProfile.model_validate({
        "schema_version": "1.0",
        "offer": {"products_services": [
            {"name": "Брус", "description": "Товар из подтверждённого каталога",
             "release_status": "live", "evidence_refs": ["product-card"]},
        ]},
    })
    plan = make_landing_opportunities(
        _core(_cluster(1, "купить брус", "transactional")), profile)
    assert plan.candidates[0].verified_offer
    assert plan.candidates[0].status == "review"


def test_unverified_profile_never_becomes_published_or_local_claim():
    plan = make_landing_opportunities(
        _core(_cluster(1, "vpn в Москве цена", "commercial")), None)
    page = plan.candidates[0]
    assert page.status == "blocked" and page.geography is None
    assert page.offer_name is None and page.reason_code == "offer_evidence_required"
    assert "Моск" in page.primary_query


def test_deduplicates_queries_blocks_existing_content_and_caps_candidates():
    core = _core(*[
        _cluster(index, f"онлайн запись цена {index}", "commercial")
        for index in range(1, 30)
    ])
    plan = make_landing_opportunities(core, _profile(),
                                      already_covered={"онлайн запись цена 1"})
    assert len(plan.candidates) == 24
    first = next(item for item in plan.candidates if item.primary_query == "онлайн запись цена 1")
    assert first.reason_code == "already_covered"
    assert len(set(page.proposed_path for page in plan.candidates)) == len(plan.candidates)
    assert make_landing_opportunities(core, _profile(), limit=0).candidates == []


def test_run_status_and_board_do_not_claim_drafts_were_published():
    from uuid import uuid4
    pid, tid = uuid4(), uuid4()
    task = Task(id=tid, product_id=pid, title="SEO завод", assigned_agent="seo_factory",
                status=TaskStatus.COMPLETED)
    draft = make_landing_opportunities(
        _core(_cluster(1, "smart dns для iphone цена", "commercial")), _profile())
    artifact = Artifact(product_id=pid, task_id=tid, kind=KIND, title="Candidates",
                        content=draft.model_dump_json())
    summary = seo_run_summary(task, [artifact])
    assert summary["landing_ready"] is True
    assert summary["landing_candidates_total"] == 1
    assert summary["landing_review_ready"] == 1
    assert summary["articles_queued"] is None
    page = seo_plan_board(summary, pid, autorun=False, enabled=True)
    assert "Programmatic SEO: посадочные страницы" in page
    assert "Предлагаемый адрес:" in page
    assert "не опубликованные URL" in page
    assert "Опубликовано</span><strong>0" in page


def test_landing_board_escapes_untrusted_query_and_path():
    from uuid import uuid4
    pid = uuid4()
    page = seo_plan_board({
        "status": "completed", "landing_ready": True,
        "landing_pages": [{
            "primary_query": "<script>alert(1)</script>",
            "proposed_path": '"><img src=x onerror=alert(1)>',
            "page_type": "offer", "reason_code": "offer_evidence_required",
            "status": "blocked",
        }],
    }, pid, autorun=False, enabled=True)
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "&lt;img" in page and "<img" not in page


def test_store_is_tenant_scoped_and_idempotent(db, tmp_path):
    first, other = Product(name="First", slug="first"), Product(name="Other", slug="other")
    db.add_all([first, other])
    db.commit()
    set_enabled(db, first.id, enabled=True, actor="owner")
    task = Task(product_id=first.id, title="Factory", assigned_agent="seo_factory",
                status=TaskStatus.RUNNING)
    db.add(task)
    db.commit()
    first_artifact = store_landing_opportunities(
        db, first.id, task.id,
        _core(_cluster(1, "купить брус", "transactional")),
        products_root=tmp_path)
    repeated = store_landing_opportunities(
        db, first.id, task.id,
        _core(_cluster(1, "купить брус", "transactional")),
        products_root=tmp_path)
    assert first_artifact.id == repeated.id
    assert len(LandingOpportunities.model_validate_json(first_artifact.content).candidates) == 1
    with pytest.raises(ValueError, match="выключен"):
        store_landing_opportunities(
            db, other.id, task.id,
            _core(_cluster(1, "купить брус", "transactional")),
            products_root=tmp_path)
