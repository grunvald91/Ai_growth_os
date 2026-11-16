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
    assert device.page_type == "device" and device.status == "blocked"
    assert device.reason_code == "device_evidence_required"
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
    assert page.offer_name is None and page.reason_code == "unverified_geography"
    assert page.page_type == "location"
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
    device_profile = _profile().model_copy(deep=True)
    device_profile.offer.products_services[1].supported_devices_evidence = {
        "iphone": "confirmed-ios-guide",
    }
    draft = make_landing_opportunities(
        _core(_cluster(1, "smart dns для iphone цена", "commercial")), device_profile)
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
    set_enabled(db, other.id, enabled=True, actor="other-owner")
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
    # Both companies explicitly enabled SEO; a foreign run must still be
    # invisible. FKs alone cannot enforce tenant ownership for task_id.
    with pytest.raises(LookupError, match="not found for company"):
        store_landing_opportunities(
            db, other.id, task.id,
            _core(_cluster(1, "купить брус", "transactional")),
            products_root=tmp_path)
    unrelated = Task(product_id=first.id, title="Unrelated",
                     assigned_agent="content_factory", status=TaskStatus.RUNNING)
    db.add(unrelated)
    db.commit()
    with pytest.raises(LookupError, match="not found for company"):
        store_landing_opportunities(
            db, first.id, unrelated.id,
            _core(_cluster(1, "купить брус", "transactional")),
            products_root=tmp_path)
    assert len(list(db.query(Artifact).filter(Artifact.kind == KIND))) == 1


def test_device_requires_per_device_evidence_even_with_live_offer():
    plan = make_landing_opportunities(
        _core(_cluster(1, "smart dns для iphone цена", "commercial")), _profile())
    assert plan.candidates[0].reason_code == "device_evidence_required"

    profile = _profile().model_copy(deep=True)
    profile.offer.products_services[1].supported_devices_evidence = {
        "iPhone": "docs/ios-setup-with-provenance",
    }
    approved = make_landing_opportunities(
        _core(_cluster(1, "smart dns для iphone цена", "commercial")), profile)
    assert approved.candidates[0].status == "review"
    assert approved.candidates[0].verified_offer

    unsupported = make_landing_opportunities(
        _core(_cluster(1, "smart dns для android цена", "commercial")), profile)
    assert unsupported.candidates[0].reason_code == "device_evidence_required"


def test_russian_device_alias_never_bypasses_evidence_gate():
    profile = _profile()
    page = make_landing_opportunities(
        _core(_cluster(1, "smart dns для айфона цена", "commercial")),
        profile,
    ).candidates[0]
    assert page.page_type == "device"
    assert page.device == "iphone"
    assert page.reason_code == "device_evidence_required"
    profile.offer.products_services[1].supported_devices_evidence = {
        "iphone": "verified-apple-profile-guide",
    }
    reviewed = make_landing_opportunities(
        _core(_cluster(1, "smart dns в айфоне цена", "commercial")),
        profile,
    ).candidates[0]
    assert reviewed.page_type == "device"
    assert reviewed.reason_code == "unique_content_required"


def test_ambiguous_common_words_are_not_evidence_for_wrong_offer():
    from app.company_profile import CompanyProfile
    profile = CompanyProfile.model_validate({
        "schema_version": "1.0",
        "offer": {"products_services": [
            {"name": "Smart DNS", "description": "service",
             "release_status": "live", "evidence_refs": ["docs/dns"]},
            {"name": "Smart Proxy", "description": "service",
             "release_status": "live", "evidence_refs": ["docs/proxy"]},
        ]},
    })
    page = make_landing_opportunities(
        _core(_cluster(1, "smart tv цена", "commercial")), profile).candidates[0]
    assert page.status == "blocked"
    assert page.offer_name is None
    assert page.reason_code == "offer_evidence_required"


def test_unlisted_city_blocks_even_with_verified_offer():
    with_preposition = make_landing_opportunities(
        _core(_cluster(1, "купить брус в Казани", "transactional")),
        CompanyProfile.model_validate({
            "schema_version": "1.0",
            "identity": {"service_geography": ["Сочи"]},
            "offer": {"products_services": [{
                "name": "Брус", "description": "available",
                "release_status": "live", "evidence_refs": ["product-card"],
            }]},
        }),
    ).candidates[0]
    assert with_preposition.page_type == "location"
    assert with_preposition.status == "blocked"
    assert with_preposition.geography is None
    assert with_preposition.reason_code == "unverified_geography"

    without_preposition = make_landing_opportunities(
        _core(_cluster(1, "Казань купить брус", "transactional")),
        _profile(),
    ).candidates[0]
    assert without_preposition.page_type == "location"
    assert without_preposition.status == "blocked"


def test_preposition_about_a_salon_is_not_a_city():
    page = make_landing_opportunities(
        _core(_cluster(1, "онлайн запись в салоне цена", "commercial")),
        _profile(),
    ).candidates[0]
    assert page.page_type == "offer"
    assert page.status == "review"


def test_long_queries_do_not_collide_after_slug_truncation():
    start = "онлайн запись " + ("очень " * 17)
    one, two = start + "первый", start + "второй"
    assert len(one) <= 300 and len(two) <= 300
    plan = make_landing_opportunities(
        _core(_cluster(1, one, "commercial"),
              _cluster(2, two, "commercial")), _profile())
    assert len(plan.candidates) == 2
    urls = [candidate.proposed_path for candidate in plan.candidates]
    assert len(set(urls)) == 2
    assert all(len(url.split("/")[-1]) <= 80 for url in urls)


def test_hyphen_and_space_queries_do_not_create_duplicate_drafts():
    from app.seo.landing import intent_key
    old = make_landing_opportunities(
        _core(_cluster(1, "онлайн-запись цена", "commercial")), _profile())
    previous = {intent_key(old.candidates[0].primary_query): old.candidates[0]}
    current = make_landing_opportunities(
        _core(_cluster(2, "онлайн запись цена", "commercial")),
        _profile(), prior_candidates=previous)
    assert current.candidates == []




def test_equivalent_price_wording_in_different_runs_is_a_single_search_intent():
    from app.seo.landing import intent_key
    old = make_landing_opportunities(
        _core(_cluster(1, "онлайн запись цена", "commercial")), _profile())
    new = make_landing_opportunities(
        _core(_cluster(2, "стоимость онлайн записи", "commercial")),
        _profile(), prior_candidates={
            intent_key(old.candidates[0].primary_query): old.candidates[0],
        })
    assert len(old.candidates) == 1
    assert new.candidates == []


def test_different_product_sizes_remain_distinct_query_intents():
    plan = make_landing_opportunities(_core(
        _cluster(1, "купить брус 50х50", "transactional"),
        _cluster(2, "купить брус 100х100", "transactional"),
    ), _profile())
    assert len(plan.candidates) == 2
    assert plan.candidates[0].proposed_path != plan.candidates[1].proposed_path


def test_common_commercial_preposition_is_not_mistaken_for_location():
    page = make_landing_opportunities(
        _core(_cluster(1, "онлайн запись по цене", "commercial")),
        _profile(),
    ).candidates[0]
    assert page.page_type == "offer"
    assert page.reason_code == "unique_content_required"


def test_two_factory_runs_deduplicate_and_recheck_blocked_offer_when_profile_changes(db, tmp_path):
    from app.seo.landing import canonical_query
    import json
    p = Product(name="Wood shop", slug="wood-shop")
    db.add(p)
    db.commit()
    set_enabled(db, p.id, enabled=True, actor="owner")
    folder = tmp_path / p.slug
    folder.mkdir(parents=True)
    profile_file = folder / "company-profile.json"

    def write_profile(status):
        profile_file.write_text(json.dumps({
            "schema_version": "1.0",
            "identity": {"category": "wood sales"},
            "offer": {"products_services": [{
                "name": "Брус", "description": "timber",
                "release_status": status,
                **({"evidence_refs": ["confirmed-product-card"]}
                   if status == "live" else {}),
            }]},
        }), encoding="utf-8")

    def run():
        task = Task(product_id=p.id, title="SEO factory",
                    assigned_agent="seo_factory", status=TaskStatus.RUNNING)
        db.add(task)
        db.commit()
        return store_landing_opportunities(
            db, p.id, task.id,
            _core(_cluster(1, "купить брус", "transactional")),
            products_root=tmp_path)

    write_profile("in_development")
    first = run()
    initial = LandingOpportunities.model_validate_json(first.content)
    assert initial.candidates[0].status == "blocked"
    assert initial.candidates[0].revision == 1

    second = run()
    assert LandingOpportunities.model_validate_json(second.content).candidates == []

    write_profile("live")
    revised = run()
    plan = LandingOpportunities.model_validate_json(revised.content)
    assert len(plan.candidates) == 1
    assert plan.candidates[0].status == "review"
    assert plan.candidates[0].revision == 2
    assert plan.candidates[0].proposed_path == initial.candidates[0].proposed_path
    assert canonical_query(plan.candidates[0].primary_query) == canonical_query(
        initial.candidates[0].primary_query)

    next_run = run()
    assert LandingOpportunities.model_validate_json(next_run.content).candidates == []

    other = Product(name="Other", slug="other-wood")
    db.add(other)
    db.commit()
    set_enabled(db, other.id, enabled=True, actor="owner")
    new_task = Task(product_id=other.id, title="SEO factory",
                    assigned_agent="seo_factory", status=TaskStatus.RUNNING)
    db.add(new_task)
    db.commit()
    # The same query in another company must NOT be blocked by prior candidates.
    separate = store_landing_opportunities(
        db, other.id, new_task.id,
        _core(_cluster(1, "купить брус", "transactional")),
        products_root=tmp_path)
    assert len(LandingOpportunities.model_validate_json(separate.content).candidates) == 1
