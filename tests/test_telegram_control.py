import pytest

from app.control.decisions import ApprovalDecisionError, decide_approval
from app.control.telegram import render_experiments
from app.models.domain import Approval, Experiment, Product
from app.services.control import ControlService
from app.telegram_control_bot import MAIN_MENU, STATUS_INLINE, TelegramAPI


def _product(db):
    product = Product(name="ServGo", slug="servgo")
    db.add(product)
    db.flush()
    return product


def test_yellow_approval_can_be_decided_from_control_flow(db):
    product = _product(db)
    approval = Approval(
        product_id=product.id,
        action_type="start_outreach",
        risk_level="yellow",
        status="pending",
    )
    db.add(approval)
    db.commit()

    decided = decide_approval(
        db,
        approval.id,
        decision="approved",
        actor="telegram:123",
    )

    assert decided.status == "approved"
    assert decided.decided_by == "telegram:123"
    assert decided.decided_at is not None


def test_red_approval_cannot_be_approved_with_one_tap(db):
    product = _product(db)
    approval = Approval(
        product_id=product.id,
        action_type="change_pricing",
        risk_level="red",
        status="pending",
    )
    db.add(approval)
    db.commit()

    with pytest.raises(ApprovalDecisionError):
        decide_approval(
            db,
            approval.id,
            decision="approved",
            actor="telegram:123",
        )

    db.refresh(approval)
    assert approval.status == "pending"

    item = ControlService(db).inbox(product.id)[0]
    assert [action.key for action in item.actions] == ["reject", "open"]


def test_rejecting_red_approval_is_allowed(db):
    product = _product(db)
    approval = Approval(
        product_id=product.id,
        action_type="delete_data",
        risk_level="red",
        status="pending",
    )
    db.add(approval)
    db.commit()

    decided = decide_approval(
        db,
        approval.id,
        decision="rejected",
        actor="telegram:123",
    )

    assert decided.status == "rejected"


def test_telegram_api_uses_proxy_from_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://proxy.example:8891")
    api = TelegramAPI("test-token")
    assert api.proxy_url == "http://proxy.example:8891"


def test_explicit_telegram_proxy_overrides_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_PROXY_URL", "http://proxy.example:8891")
    api = TelegramAPI("test-token", "http://override.example:9999")
    assert api.proxy_url == "http://override.example:9999"



def test_button_menu_exposes_primary_controls():
    labels = [button["text"] for row in MAIN_MENU["keyboard"] for button in row]
    assert labels == ["📊 Статус", "📨 Решения", "🧪 Эксперименты", "🔄 Обновить"]
    callbacks = [
        button["callback_data"]
        for row in STATUS_INLINE["inline_keyboard"]
        for button in row
    ]
    assert callbacks == ["menu:inbox", "menu:experiments", "menu:status"]


def test_render_experiments_shows_current_experiment(db):
    product = _product(db)
    db.add(
        Experiment(
            product_id=product.id,
            experiment_key="EXP-SERVGO-001",
            name="Direct outreach",
            hypothesis="Test demand",
            target_metric="qualified_leads",
            status="draft",
        )
    )
    db.commit()

    status = ControlService(db).all_status()
    text = render_experiments(status)

    assert "EXP-SERVGO-001" in text
    assert "qualified_leads" in text
    assert "Direct outreach" in text
