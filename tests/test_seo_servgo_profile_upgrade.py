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
