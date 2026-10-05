import json
from pathlib import Path

from sqlalchemy import func, select

from app.bootstrap import (
    SERVGO_GROWTH_PLAN_MARKER,
    SERVGO_GROWTH_PROFILE_MIGRATION,
    apply_servgo_growth_profile_migration,
)
from app.company_profile import load_company_profile
from app.models.domain import Artifact, Product, Task, TaskStatus


OLD_PROFILE = {
    "schema_version": "1.0",
    "identity": {
        "name": "ServGo",
        "one_liner": "ServGo is an online service booking/reservation system for small service businesses.",
    },
    "offer": {
        "products_services": [{
            "name": "ServGo online booking",
            "description": "Verified booking flow remains available.",
        }]
    },
    "commercial": {"pricing_model": "One-time setup plus monthly service", "currency": "RUB"},
    "proof": {
        "approved_facts": [{
            "statement": "ServGo is an online service booking/reservation system for small service businesses.",
            "evidence_ref": "verified-product",
        }],
        "approved_numbers": [
            {"statement": "Implementation/setup: 9,900 RUB one-time.", "evidence_ref": "legacy"},
            {"statement": "Monthly service: 1,490 RUB for up to 5 employees.", "evidence_ref": "legacy"},
            {"statement": "Monthly service: 3,000 RUB for 6 to 15 employees.", "evidence_ref": "legacy"},
        ],
    },
    "brand": {"prohibited_claims": ["Unverified product features"]},
    "constraints": {
        "privacy": ["Do not send customer PII."],
        "operational": ["Keep facts separate from hypotheses."],
        "approval_rules": ["Paid actions require approval."],
    },
    "hypotheses": [{
        "statement": "Massage studios is a candidate segment to test.",
        "rationale": "legacy",
    }],
    "unknowns": [
        {"question": "Approved primary customer segments"},
        {"question": "Owner-specific unknown that must survive"},
    ],
}


def test_growth_profile_migration_is_targeted_durable_and_idempotent(db, tmp_path):
    product = Product(name="ServGo", slug="servgo", description="demo")
    db.add(product)
    db.commit()

    runtime_root = tmp_path / "runtime"
    path = runtime_root / "servgo" / "company-profile.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(OLD_PROFILE, ensure_ascii=False), encoding="utf-8")

    packaged_root = Path(__file__).resolve().parents[1] / "products"
    changed = apply_servgo_growth_profile_migration(
        db, product, runtime_root=runtime_root, packaged_root=packaged_root
    )
    assert changed is True

    profile = load_company_profile(runtime_root, "servgo").profile
    assert profile is not None
    assert "ServGo Growth" in profile.brand.positioning
    assert "under review" in profile.commercial.pricing_model.lower()
    assert profile.proof.approved_numbers == []
    assert profile.customers.primary_segments
    assert profile.offer.products_services[0].description == "Verified booking flow remains available."
    assert any(
        row.question == "Owner-specific unknown that must survive"
        for row in profile.unknowns
    )
    assert not any(
        row.question == "Approved primary customer segments"
        for row in profile.unknowns
    )

    previous = db.scalar(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind == "company_profile_previous",
    ))
    assert previous is not None
    previous_payload = json.loads(previous.content)
    assert any(
        item["statement"].startswith("Implementation/setup")
        for item in previous_payload["profile"]["proof"]["approved_numbers"]
    )

    marker = db.scalar(select(Artifact).where(
        Artifact.product_id == product.id,
        Artifact.kind == "company_profile_migration",
        Artifact.title == SERVGO_GROWTH_PROFILE_MIGRATION,
    ))
    assert marker is not None

    plan_task = db.scalar(select(Task).where(
        Task.product_id == product.id,
        Task.assigned_agent == "editorial_planner",
        Task.description.contains(SERVGO_GROWTH_PLAN_MARKER),
    ))
    assert plan_task is not None
    assert plan_task.status == TaskStatus.QUEUED

    artifact_count = db.scalar(select(func.count()).select_from(Artifact))
    task_count = db.scalar(select(func.count()).select_from(Task))
    assert apply_servgo_growth_profile_migration(
        db, product, runtime_root=runtime_root, packaged_root=packaged_root
    ) is False
    assert db.scalar(select(func.count()).select_from(Artifact)) == artifact_count
    assert db.scalar(select(func.count()).select_from(Task)) == task_count


def test_growth_profile_migration_preserves_existing_verified_capabilities(db, tmp_path):
    product = Product(name="ServGo", slug="servgo", description="demo")
    db.add(product)
    db.commit()

    custom = json.loads(json.dumps(OLD_PROFILE))
    custom["offer"]["products_services"].append({
        "name": "Owner-confirmed capability",
        "description": "Keep this verified capability during positioning migration.",
    })
    custom["proof"]["approved_facts"].append({
        "statement": "Owner-confirmed capability exists.",
        "evidence_ref": "owner-confirmed",
    })

    runtime_root = tmp_path / "runtime"
    path = runtime_root / "servgo" / "company-profile.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")

    apply_servgo_growth_profile_migration(
        db,
        product,
        runtime_root=runtime_root,
        packaged_root=Path(__file__).resolve().parents[1] / "products",
    )
    profile = load_company_profile(runtime_root, "servgo").profile
    assert [item.name for item in profile.offer.products_services][-1] == "Owner-confirmed capability"
    assert any(item.statement == "Owner-confirmed capability exists." for item in profile.proof.approved_facts)
