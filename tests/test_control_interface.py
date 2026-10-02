from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.control.telegram import render_decision_required, render_status
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Approval, Experiment, Product


def test_control_status_and_inbox_are_derived_from_canonical_records():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        product = Product(name="ServGo", slug="servgo")
        db.add(product)
        db.flush()
        experiment = Experiment(
            product_id=product.id,
            experiment_key="EXP-SERVGO-001",
            name="Direct outreach",
            hypothesis="Test qualified demand.",
            target_metric="qualified_leads",
            status="draft",
        )
        approval = Approval(
            product_id=product.id,
            action_type="start_outreach",
            risk_level="yellow",
            status="pending",
            requested_by="cmo",
            reason="30 candidates are ready; human approval is required before first contact.",
        )
        db.add_all([experiment, approval])
        db.commit()

        app.dependency_overrides[get_db] = lambda: db
        try:
            with TestClient(app) as client:
                global_status = client.get("/control/status")
                assert global_status.status_code == 200
                payload = global_status.json()
                assert payload["decisions_waiting"] == 1
                assert payload["products"][0]["product_name"] == "ServGo"
                assert payload["products"][0]["draft_experiments"] == 1
                assert payload["products"][0]["experiments"][0]["experiment_key"] == "EXP-SERVGO-001"

                inbox_response = client.get(f"/products/{product.id}/control/inbox")
                assert inbox_response.status_code == 200
                inbox = inbox_response.json()
                assert len(inbox) == 1
                assert inbox[0]["kind"] == "decision_required"
                assert inbox[0]["action_type"] == "start_outreach"
                assert [action["key"] for action in inbox[0]["actions"]] == [
                    "approve",
                    "reject",
                    "open",
                ]

                status_text = render_status(
                    __import__("app.control.contracts", fromlist=["ControlStatus"]).ControlStatus(
                        **payload
                    )
                )
                assert "AI-команда" in status_text
                assert "ServGo" in status_text
                assert "Подготовлено тестов к запуску: 1" in status_text

                item_model = __import__(
                    "app.control.contracts", fromlist=["ControlInboxItem"]
                ).ControlInboxItem(**inbox[0])
                decision_text = render_decision_required(item_model)
                assert "Нужно твоё решение" in decision_text
                assert "Начать работу с потенциальными клиентами" in decision_text

                missing = client.get(
                    "/products/00000000-0000-0000-0000-000000000000/control/status"
                )
                assert missing.status_code == 404
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
