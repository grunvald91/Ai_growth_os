import json
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.main import app
from app.db.models import Base
from app.db.session import get_db
from app.api.products import get_review
from app.core.composition import build_review
from app.core.config import Settings
from app.llm.fake import FakeLLMProvider, LLMResponse


def test_api_vertical_slice(tmp_path):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    provider = FakeLLMProvider(LLMResponse(model='fake', content=json.dumps({
        'summary': 'Organic review', 'decisions': [{'decision_type': 'TEST', 'rationale': 'No evidence yet'}]})))
    with Session(engine) as db:
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_review] = lambda: build_review(db, Settings(products_root=tmp_path), provider)
        try:
            with TestClient(app) as client:
                assert client.get('/health').status_code == 200
                response = client.post('/products', json={'name': 'ServGo', 'slug': 'servgo'})
                assert response.status_code == 201
                pid = response.json()['id']
                assert client.post('/products', json={'name': 'Duplicate', 'slug': 'servgo'}).status_code == 409
                assert client.post(f'/products/{pid}/goals', json={'name': 'Organic leads'}).status_code == 201
                assert client.get(f'/products/{pid}').json()['name'] == 'ServGo'
                assert len(client.get('/products').json()) == 1
                result = client.post(f'/products/{pid}/daily-growth-review').json()
                assert result['status'] == 'completed' and result['run_id']
                assert len(client.get(f'/products/{pid}/decisions').json()) == 1
                for kind in ['goals', 'tasks', 'experiments', 'artifacts', 'approvals']:
                    assert client.get(f'/products/{pid}/{kind}').status_code == 200
                assert client.get('/products/00000000-0000-0000-0000-000000000000').status_code == 404
                assert client.get('/products/not-a-uuid').status_code == 422
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
