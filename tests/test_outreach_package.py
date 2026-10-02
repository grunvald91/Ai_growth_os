import json

from sqlalchemy import select

from app.models.domain import Approval, Artifact, Experiment, Product, Prospect
from app.services.outreach import OutreachPackageService


def _setup(db):
    product = Product(name="ServGo", slug="servgo-outreach")
    db.add(product)
    db.flush()
    experiment = Experiment(
        product_id=product.id,
        experiment_key="EXP-SERVGO-001",
        name="Outreach",
        hypothesis="Test demand",
        status="running",
    )
    db.add(experiment)
    db.flush()
    return product, experiment


def _prospect(product, experiment, lead_key, channel, target):
    values = {
        "product_id": product.id,
        "experiment_id": experiment.id,
        "lead_key": lead_key,
        "business_name": f"Business {lead_key}",
        "validation_status": "validated",
        "outreach_status": "not_contacted",
        "preferred_channel": channel,
        "tracking_url": f"https://promo.servgo.ru/?utm_content={lead_key}",
        "notes": "у бизнеса есть подтверждённая публичная онлайн-запись.",
        "evidence": [{"field": "identity"}],
    }
    values[channel] = target
    return Prospect(**values)


def test_prepare_outreach_package_uses_only_validated_direct_channels(db):
    product, experiment = _setup(db)
    db.add_all(
        [
            _prospect(product, experiment, "SG001-004", "email", "spa@example.com"),
            _prospect(product, experiment, "SG001-005", "whatsapp", "+79990000000"),
            _prospect(product, experiment, "SG001-021", "telegram", "@example"),
            _prospect(product, experiment, "SG001-001", "website", "https://example.com"),
            Prospect(
                product_id=product.id,
                experiment_id=experiment.id,
                lead_key="SG001-999",
                business_name="Not validated",
                validation_status="pending",
                outreach_status="not_contacted",
                preferred_channel="email",
                email="pending@example.com",
                tracking_url="https://promo.servgo.ru/?utm_content=SG001-999",
                evidence=[],
            ),
        ]
    )
    db.commit()

    result = OutreachPackageService(db).prepare(experiment_id=experiment.id)

    assert result.message_count == 3
    package = json.loads(result.artifact.content)
    assert [item["lead_id"] for item in package["messages"]] == [
        "SG001-004",
        "SG001-005",
        "SG001-021",
    ]
    assert [item["channel"] for item in package["messages"]] == [
        "email",
        "whatsapp",
        "telegram",
    ]
    assert package["send_policy"]["send_only_after_explicit_approval"] is True
    assert result.approval.action_type == "send_outreach_batch"
    assert result.approval.risk_level == "YELLOW"
    assert result.approval.status == "pending"
    assert result.approval.artifact_id == result.artifact.id
    assert "does not itself send" in result.approval.reason


def test_prepare_outreach_package_is_idempotent(db):
    product, experiment = _setup(db)
    db.add(_prospect(product, experiment, "SG001-004", "email", "spa@example.com"))
    db.commit()

    first = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    second = OutreachPackageService(db).prepare(experiment_id=experiment.id)

    assert first.artifact.id == second.artifact.id
    assert first.approval.id == second.approval.id
    assert len(list(db.scalars(select(Artifact).where(Artifact.kind == "outreach_package")))) == 1
    assert len(
        list(
            db.scalars(
                select(Approval).where(Approval.action_type == "send_outreach_batch")
            )
        )
    ) == 1


def test_prepare_outreach_package_never_marks_prospect_contacted(db):
    product, experiment = _setup(db)
    prospect = _prospect(product, experiment, "SG001-004", "email", "spa@example.com")
    db.add(prospect)
    db.commit()

    OutreachPackageService(db).prepare(experiment_id=experiment.id)
    db.refresh(prospect)

    assert prospect.outreach_status == "not_contacted"
    assert prospect.last_contact_at is None
