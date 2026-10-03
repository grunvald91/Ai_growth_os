import json
import pytest
from types import SimpleNamespace

from sqlalchemy import select

from app.models.domain import Approval, Artifact, Experiment, Product, Prospect
from app.services.outreach import OutreachPackageService
from app.company_profile import CompanyProfileNotReady


@pytest.fixture(autouse=True)
def supplied_profile(tmp_path, monkeypatch):
    payload = {
        "schema_version": "1.0",
        "identity": {"name": "ServGo", "one_liner": "Online booking", "languages": ["ru"]},
        "offer": {"products_services": [{"name": "Booking", "description": "Online booking"}],
                  "primary_conversion_action": "Запросить демо"},
        "customers": {"primary_segments": ["Service businesses"]},
        "brand": {"prohibited_claims": []},
        "constraints": {"privacy": [], "legal_compliance": [], "operational": [], "approval_rules": []},
        "outreach": {"icp_criteria": ["Service businesses"], "allowed_channels": ["email", "whatsapp", "telegram"],
                     "sender_identity": "Артур, GrunvaldLab", "approval_required": True, "do_not_contact": []},
    }
    product = tmp_path / "servgo-outreach"
    product.mkdir()
    path = product / "company-profile.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("app.services.outreach.get_settings", lambda: SimpleNamespace(products_root=tmp_path))
    return path


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



def test_prepare_outreach_package_includes_more_than_ten_ready_prospects(db):
    product, experiment = _setup(db)
    for index in range(1, 16):
        db.add(
            _prospect(
                product,
                experiment,
                f"SG001-{index:03d}",
                "email",
                f"lead{index}@example.com",
            )
        )
    db.commit()

    result = OutreachPackageService(db).prepare(experiment_id=experiment.id)

    assert result.message_count == 15
    package = json.loads(result.artifact.content)
    assert len(package["messages"]) == 15


def test_new_package_creates_new_approval_and_supersedes_stale(db):
    product, experiment = _setup(db)
    prospect = _prospect(product, experiment, "SG001-004", "email", "old@example.com")
    db.add(prospect)
    db.commit()

    first = OutreachPackageService(db).prepare(experiment_id=experiment.id)

    prospect.email = "new@example.com"
    prospect.evidence = [{"field": "identity"}, {"field": "email"}]
    db.commit()

    second = OutreachPackageService(db).prepare(experiment_id=experiment.id)

    assert first.approval.id != second.approval.id
    assert first.approval.artifact_id == first.artifact.id
    assert first.approval.status == "rejected"
    assert second.approval.status == "pending"
    assert second.approval.artifact_id == second.artifact.id
    pending = list(
        db.scalars(
            select(Approval).where(
                Approval.action_type == "send_outreach_batch",
                Approval.status == "pending",
            )
        )
    )
    assert len(pending) == 1


def test_old_button_cannot_approve_replacement_package(db):
    from app.control.decisions import decide_approval, approval_review_token, ApprovalDecisionError
    product, experiment = _setup(db)
    prospect = _prospect(product, experiment, "lead1", "email", "old@example.com")
    db.add(prospect)
    db.commit()
    first = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    token = approval_review_token(db, first.approval)
    prospect.email = "changed@example.com"
    db.commit()
    second = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    with pytest.raises(ApprovalDecisionError, match="заменён"):
        decide_approval(db, first.approval.id, decision="approved", actor="owner",
                        expected_review_token=token)
    assert second.approval.status == "pending"


def test_two_experiments_keep_independent_pending_approvals(db):
    product, experiment = _setup(db)
    second_experiment = Experiment(product_id=product.id, experiment_key="EXP-SECOND",
        name="Second", hypothesis="Second", status="running")
    db.add(second_experiment)
    db.flush()
    db.add_all([_prospect(product, experiment, "lead1", "email", "first@example.com"),
                _prospect(product, second_experiment, "lead2", "email", "second@example.com")])
    db.commit()
    first = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    second = OutreachPackageService(db).prepare(experiment_id=second_experiment.id)
    assert first.approval.id != second.approval.id
    assert first.approval.status == second.approval.status == "pending"
    assert first.approval.artifact_id == first.artifact.id


def test_outreach_decision_requires_unchanged_review_and_immutable_binding(db):
    from app.control.decisions import decide_approval, approval_review_token, ApprovalDecisionError
    from app.services.approvals import ApprovalService
    from app.services.artifacts import ArtifactService
    product, experiment = _setup(db)
    db.add(_prospect(product, experiment, "lead1", "email", "first@example.com"))
    db.commit()
    package = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    token = approval_review_token(db, package.approval)
    assert len(f"approve:{package.approval.id}:{token}".encode()) <= 64
    with pytest.raises(ValueError, match="immutable"):
        ApprovalService(db).update(package.approval.id, artifact_id=None)
    with pytest.raises(ValueError, match="immutable"):
        ArtifactService(db).update(package.artifact.id, content="changed")
    with pytest.raises(ApprovalDecisionError, match="изменился"):
        decide_approval(db, package.approval.id, decision="approved", actor="old-button")
    original = package.artifact.content
    package.artifact.content += " "
    db.commit()
    with pytest.raises(ApprovalDecisionError, match="изменился"):
        decide_approval(db, package.approval.id, decision="approved", actor="owner",
                        expected_review_token=token)
    package.artifact.content = original
    db.commit()
    decided = decide_approval(db, package.approval.id, decision="approved", actor="owner",
                              expected_review_token=token)
    assert decided.status == "approved"


@pytest.mark.asyncio
async def test_telegram_buttons_bind_review_and_reject_changed_content(db, monkeypatch):
    from app.telegram_control_bot import TelegramControlBot
    product, experiment = _setup(db)
    db.add(_prospect(product, experiment, "lead1", "email", "first@example.com"))
    db.commit()
    package = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    class Borrow:
        def __enter__(self): return db
        def __exit__(self, *args): pass
    class API:
        def __init__(self): self.calls = []
        async def call(self, method, payload):
            self.calls.append((method, payload))
            return {}
    monkeypatch.setattr("app.telegram_control_bot.SessionLocal", lambda: Borrow())
    api = API()
    bot = TelegramControlBot(api, owner_chat_id=42)
    await bot._send_inbox(42)
    keyboard = next(payload["reply_markup"]["inline_keyboard"] for method, payload in api.calls
                    if method == "sendMessage" and "Что предлагается" in payload["text"])
    callback = next(button["callback_data"] for row in keyboard for button in row
                    if button["callback_data"].startswith("approve:"))
    assert len(callback.encode()) <= 64 and len(callback.split(":")) == 3
    original = package.artifact.content
    package.artifact.content += " "
    db.commit()
    update = {"id": "callback", "from": {"id": 42}, "message": {"chat": {"id": 42}}, "data": callback}
    await bot._handle_callback(update)
    assert package.approval.status == "pending"
    assert any("изменился" in payload.get("text", "") for _, payload in api.calls)
    package.artifact.content = original
    db.commit()
    await bot._handle_callback(update)
    assert package.approval.status == "approved"


@pytest.mark.parametrize("invalid", ["missing", "malformed", "incomplete", "legacy_only"])
def test_unready_profile_blocks_without_mutation(db, supplied_profile, invalid):
    product, experiment = _setup(db)
    prospect = _prospect(product, experiment, "SG001-004", "email", "spa@example.com")
    db.add(prospect)
    db.commit()
    if invalid in {"missing", "legacy_only"}:
        supplied_profile.unlink()
        if invalid == "legacy_only":
            legacy = supplied_profile.parent / ".claude"
            legacy.mkdir()
            (legacy / "product-marketing-context.md").write_text("Legacy ServGo")
    elif invalid == "malformed":
        supplied_profile.write_text("{bad-json")
    else:
        data = json.loads(supplied_profile.read_text())
        del data["outreach"]["sender_identity"]
        supplied_profile.write_text(json.dumps(data))
    with pytest.raises(CompanyProfileNotReady) as error:
        OutreachPackageService(db).prepare(experiment_id=experiment.id)
    assert error.value.gate == "outreach"
    assert "outreach.sender_identity" in error.value.missing_fields
    assert list(db.scalars(select(Artifact))) == []
    assert list(db.scalars(select(Approval))) == []
    db.refresh(experiment)
    db.refresh(prospect)
    assert experiment.status == "running"
    assert prospect.outreach_status == "not_contacted"


def test_profile_controls_channels_sender_and_claims(db, supplied_profile):
    data = json.loads(supplied_profile.read_text())
    data["identity"]["name"] = "Example"
    data["identity"]["one_liner"] = "Verified service description"
    data["outreach"]["sender_identity"] = "Example founder"
    data["outreach"]["allowed_channels"] = ["email"]
    supplied_profile.write_text(json.dumps(data))
    product, experiment = _setup(db)
    db.add_all([_prospect(product, experiment, "SG001-004", "email", "spa@example.com"),
                _prospect(product, experiment, "SG001-005", "telegram", "@example")])
    db.commit()
    result = OutreachPackageService(db).prepare(experiment_id=experiment.id)
    messages = json.loads(result.artifact.content)["messages"]
    assert len(messages) == 1 and messages[0]["channel"] == "email"
    assert "Example founder" in messages[0]["body"]
    assert "Verified service description" in messages[0]["body"]
    assert "ServGo" not in messages[0]["body"]
    assert "напоминания" not in messages[0]["body"]
