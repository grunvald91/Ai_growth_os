import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.company_intake import get_intake, router
from app.intake.service import CompanyIntakeService
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.models.domain import Product
from app.db.models import Base
from app.privacy.gateway import PrivacyGateway


def test_api_start_sources_preview_apply_requires_exact_explicit_review(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name="Example", slug="example")
        db.add(product)
        db.commit()
        provider = FakeLLMProvider(LLMResponse(content='{"claims":[]}', model="kimi-2.7"))
        service = CompanyIntakeService(db, products_root=tmp_path,
            llm=LLMService(privacy_gateway=PrivacyGateway(), provider=provider))
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_intake] = lambda: service
        with TestClient(app) as client:
            prefix = f"/products/{product.id}/intake"
            start = client.post(prefix)
            assert start.status_code == 200
            session = start.json()["session_id"]
            path = prefix + "/" + session
            source = client.post(path + "/sources", json={
                "kind": "text", "text": "Company Example"}).json()["sources"][-1]
            provider.response = LLMResponse(model="kimi-2.7", content=json.dumps({"claims": [{
                "source_id": source["id"], "field_path": "identity.name",
                "proposed_value": "Example", "evidence_excerpt": "Company Example",
                "kind": "candidate",
            }]}))
            draft = client.post(path + "/draft")
            assert draft.status_code == 200
            review_id = draft.json()["review_id"]
            canonical = tmp_path / "example/company-profile.json"
            assert not canonical.exists()
            for value in [False, 1, "true"]:
                response = client.post(path + "/approve", json={"review_id": review_id, "confirmed": value})
                assert response.status_code == 422
            assert not canonical.exists()
            applied = client.post(path + "/approve", json={"review_id": review_id, "confirmed": True})
            assert applied.status_code == 200 and canonical.exists()
            assert client.get(path).json()["status"] == "completed"
            assert client.post(path + "/sources", json={"kind": "text", "text": "Late"}).status_code == 409
            other = client.post("/products/00000000-0000-0000-0000-000000000001/intake")
            assert other.status_code == 404
    engine.dispose()
