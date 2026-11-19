"""Regression tests for safe, owner-preserving ServGo SEO profile upgrade."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from app.seo.upgrade_servgo_profile import merge_profile, upgrade


def packaged():
    path = Path(__file__).resolve().parents[1] / "products/servgo/company-profile.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_merge_preserves_owner_custom_and_is_idempotent():
    current = copy.deepcopy(packaged())
    current["identity"]["one_liner"] = "Owner customized summary"
    current["identity"]["category"] = "Custom owner category"
    current["commercial"]["pricing_model"] = "Owner-priced"
    current["brand"]["positioning"] = "Owner positioning"
    current["proof"]["approved_facts"] = [
        {"statement": "Owner verified fact", "evidence_ref": "owner"},
    ]
    current["customers"]["primary_segments"] = ["Owner segment"]
    current["unknowns"].append({"question": "Owner secret unknown"})
    merged, stats = merge_profile(current, packaged())
    assert merged["identity"]["one_liner"] == "Owner customized summary"
    assert merged["identity"]["category"] == "Custom owner category"
    assert merged["commercial"]["pricing_model"] == "Owner-priced"
    assert "Owner positioning" in merged["brand"]["positioning"]
    assert "повторные обращения" in merged["brand"]["positioning"]
    assert any(f["statement"] == "Owner verified fact" for f in merged["proof"]["approved_facts"])
    assert any(x["question"] == "Owner secret unknown" for x in merged["unknowns"])
    assert "Owner segment" in merged["customers"]["primary_segments"]
    second, repeat = merge_profile(merged, packaged())
    assert second == merged
    assert not repeat["positioning"] and repeat["facts"] == 0


def test_known_stock_description_is_upgraded_without_overwriting_custom_text():
    current = packaged()
    current["identity"]["one_liner"] = (
        "ServGo is an online service booking/reservation system for small service businesses."
    )
    current["identity"]["category"] = "B2B SaaS / online booking for service businesses"
    merged, _ = merge_profile(current, packaged())
    assert "собственная база клиентов" in merged["identity"]["one_liner"]
    assert "CRM" in merged["identity"]["category"]


def test_dry_run_backup_hash_guard_and_repeat(tmp_path):
    p = tmp_path / "live"
    p.mkdir()
    target = p / "company-profile.json"
    source = tmp_path / "packaged.json"
    packaged_data = packaged()
    source.write_text(json.dumps(packaged_data, ensure_ascii=False), encoding="utf-8")
    live_data = copy.deepcopy(packaged_data)
    live_data["brand"]["positioning"] = "Approved by owner earlier"
    target.write_text(json.dumps(live_data, ensure_ascii=False), encoding="utf-8")
    before = target.read_bytes()
    sha = hashlib.sha256(before).hexdigest()
    check = upgrade(target, source, apply=False)
    assert check["changed"] and not check["applied"]
    assert target.read_bytes() == before
    with pytest.raises(ValueError, match="changed since preflight"):
        upgrade(target, source, apply=True, expected_sha256="wrong")
    assert target.read_bytes() == before
    result = upgrade(target, source, apply=True, expected_sha256=sha)
    assert result["applied"]
    assert (p / result["backup"]).read_bytes() == before
    assert not upgrade(target, source, apply=False)["changed"]
    assert not upgrade(target, source, apply=True)["applied"]


def test_legacy_runtime_profile_receives_product_catalog_without_replacing_owner_text():
    current = copy.deepcopy(packaged())
    current["offer"]["products_services"] = current["offer"]["products_services"][:4]
    for offer in current["offer"]["products_services"]:
        for key in ("release_status", "product_family", "evidence_refs",
                    "buyer_segment", "conversion_action"):
            offer.pop(key, None)
    current["offer"]["products_services"][0]["description"] = "Owner customized booking explanation"
    current["proof"]["approved_facts"] = []
    current["unknowns"] = []
    updated, stats = merge_profile(current, packaged())
    offers = updated["offer"]["products_services"]
    assert stats["offers"] == 4
    assert stats["catalog_fields"] >= 4
    assert offers[0]["description"] == "Owner customized booking explanation"
    assert {o["product_family"] for o in offers} == {
        "BookingBot", "Growth", "Mail", "Смена", "SEO-завод"}
    assert next(o for o in offers if o["product_family"] == "Смена")["release_status"] == "live"
    assert any("SEO-завод" in fact["statement"] for fact in updated["proof"]["approved_facts"])
    again, count = merge_profile(updated, packaged())
    assert again == updated
    assert count["offers"] == 0 and count["catalog_fields"] == 0



def test_correct_previous_shifts_false_negative_without_losing_owner_edits():
    current = copy.deepcopy(packaged())
    shift = next(o for o in current["offer"]["products_services"]
                 if o.get("product_family") == "Смена")
    shift["release_status"] = "unverified"
    shift["evidence_refs"] = []
    shift["conversion_action"] = None
    shift["description"] = (
        "Модуль учёта смен рассматривается как направление платформы. "
        "Действующая версия, точный функционал и доступность для клиентов не подтверждены "
        "текущим аудитом исходного кода и продакшена. Не объявлять продукт запущенным "
        "и не предлагать его подключить до отдельной технической проверки."
    )
    current["unknowns"].append({"question":
        "Проверен ли боевой выпуск «Смены», включая Telegram mini-app, QR/selfie "
        "и учёт рабочего времени? Без подтверждённых исходников и smoke-теста "
        "не рекламировать как действующий модуль."})
    custom = {"statement": "Owner independent verified statement", "evidence_ref": "owner"}
    current["proof"]["approved_facts"].append(custom)
    current["commercial"]["pricing_model"] = "Custom owner pricing"
    corrected, stats = merge_profile(current, packaged())
    actual = next(o for o in corrected["offer"]["products_services"]
                  if o.get("product_family") == "Смена")
    assert actual["release_status"] == "live"
    assert "owner-confirmation:servgo" in actual["evidence_refs"][0]
    assert "Работающий модуль" in actual["description"]
    assert any("«Смена» работает" in f["statement"]
               for f in corrected["proof"]["approved_facts"])
    assert not any("Не объявлять продукт запущенным" in o["description"]
                   for o in corrected["offer"]["products_services"])
    assert corrected["commercial"]["pricing_model"] == "Custom owner pricing"
    assert any(f["statement"] == custom["statement"] and f["evidence_ref"] == "owner" for f in corrected["proof"]["approved_facts"])
    assert stats["stock_corrections"] >= 2
    repeated, again = merge_profile(corrected, packaged())
    assert repeated == corrected
    assert again["stock_corrections"] == 0


def test_manual_owner_shifts_description_is_not_overwritten():
    current = copy.deepcopy(packaged())
    shift = next(o for o in current["offer"]["products_services"]
                 if o.get("product_family") == "Смена")
    shift["release_status"] = "unverified"
    shift["evidence_refs"] = []
    shift["description"] = "Custom owner edited description; availability requires signoff"
    shift["conversion_action"] = None
    updated, _ = merge_profile(current, packaged())
    actual = next(o for o in updated["offer"]["products_services"]
                  if o.get("product_family") == "Смена")
    assert actual["description"] == "Custom owner edited description; availability requires signoff"
    assert actual["release_status"] == "unverified"


def test_live_shifts_uses_owner_confirmation_not_unverified_repository():
    data = packaged()
    shift = next(o for o in data["offer"]["products_services"]
                 if o.get("product_family") == "Смена")
    assert shift["release_status"] == "live"
    assert shift["evidence_refs"] == ["owner-confirmation:servgo:2026-11-03:shifts-operational"]
    assert "не подтверждены текущим аудитом" not in shift["description"]


def test_persistent_servgo_profile_replaces_only_known_stale_editorial_facts():
    from app.seo.upgrade_servgo_profile import (
        STOCK_FACT_REPLACEMENTS, STOCK_OFFER_REPLACEMENTS,
        LEGACY_POSITIONING_TAIL, NEW_POSITIONING_TAIL,
        LEGACY_POSITIONING_ADDENDUM, POSITIONING_ADDENDUM,
    )
    current = copy.deepcopy(packaged())
    custom_fact = {"statement": "Custom CRM claim from owner", "evidence_ref": "owner:proof"}
    current["proof"]["approved_facts"].append(custom_fact)
    for item in current["proof"]["approved_facts"]:
        for old, new in STOCK_FACT_REPLACEMENTS.items():
            if item["statement"] == new:
                item["statement"] = old
    for offer in current["offer"]["products_services"]:
        for old, new in STOCK_OFFER_REPLACEMENTS.items():
            if offer.get("description") == new:
                offer["description"] = old
    assert NEW_POSITIONING_TAIL in current["brand"]["positioning"]
    current["brand"]["positioning"] = (
        current["brand"]["positioning"].replace(
            NEW_POSITIONING_TAIL, LEGACY_POSITIONING_TAIL
        ) + LEGACY_POSITIONING_ADDENDUM
    )
    current["constraints"]["operational"] = [
        rule for rule in current["constraints"]["operational"]
        if not rule.startswith("Не ")
    ]
    current["unknowns"] = [
        q for q in current["unknowns"]
        if "автоматическая отправка сообщения из CRM" not in q["question"]
    ]
    corrected, stats = merge_profile(current, packaged())
    statements = {f["statement"] for f in corrected["proof"]["approved_facts"]}
    assert set(STOCK_FACT_REPLACEMENTS.values()).issubset(statements)
    assert not set(STOCK_FACT_REPLACEMENTS).intersection(statements)
    assert any(f["statement"] == custom_fact["statement"] and f["evidence_ref"] == custom_fact["evidence_ref"]
               for f in corrected["proof"]["approved_facts"])
    descriptions = {o["description"] for o in corrected["offer"]["products_services"]}
    assert not set(STOCK_OFFER_REPLACEMENTS).intersection(descriptions)
    assert set(STOCK_OFFER_REPLACEMENTS.values()).issubset(descriptions)
    assert LEGACY_POSITIONING_TAIL not in corrected["brand"]["positioning"]
    assert LEGACY_POSITIONING_ADDENDUM.strip() not in corrected["brand"]["positioning"]
    assert POSITIONING_ADDENDUM.strip() in corrected["brand"]["positioning"]
    assert any("автоматические рассылки" in r for r in corrected["constraints"]["operational"])
    assert stats["stock_corrections"] >= 8
    assert merge_profile(corrected, packaged())[0] == corrected


def test_owner_customized_fact_is_never_replaced_by_stock_upgrade():
    from app.seo.upgrade_servgo_profile import STOCK_FACT_REPLACEMENTS
    current = copy.deepcopy(packaged())
    old_text = next(iter(STOCK_FACT_REPLACEMENTS))
    current["proof"]["approved_facts"] = [
        {"statement": old_text, "evidence_ref": "owner:independent-source"}
    ]
    updated, _ = merge_profile(current, packaged())
    assert any(f["statement"] == old_text and f["evidence_ref"] == "owner:independent-source"
               for f in updated["proof"]["approved_facts"])
