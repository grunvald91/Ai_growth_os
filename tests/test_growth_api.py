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
                goal_response = client.post(f'/products/{pid}/goals', json={'name': 'Organic leads'})
                assert goal_response.status_code == 201
                goal_id = goal_response.json()['id']
                experiment_body = {
                    'experiment_key': 'EXP-SERVGO-001',
                    'goal_id': goal_id,
                    'name': 'Direct outreach validation',
                    'hypothesis': 'A bounded direct-outreach cohort may produce qualified ServGo leads.',
                    'audience': 'Small appointment-based service businesses',
                    'channel': 'direct_outreach',
                    'action': 'Contact one bounded cohort with a tracked ServGo evaluation link.',
                    'target_metric': 'qualified_leads',
                    'secondary_signals': ['positive_replies', 'demo_requests'],
                    'attribution_method': 'experiment_id + UTM + self-report + manual verification',
                    'baseline_summary': 'Website traffic, marketing leads and CAC are currently UNKNOWN.',
                    'observation_window_days': 14,
                    'time_budget_minutes': 240,
                    'success_condition': 'KEEP if the predefined qualified-lead threshold is met.',
                    'stop_condition': 'STOP if the full cohort produces zero qualified leads after the observation window.',
                    'inconclusive_condition': 'INCONCLUSIVE if the cohort or attribution coverage is incomplete.',
                    'budget': '0',
                    'currency': 'RUB',
                }
                experiment_response = client.post(f'/products/{pid}/experiments', json=experiment_body)
                assert experiment_response.status_code == 201, experiment_response.text
                experiment = experiment_response.json()
                assert experiment['experiment_key'] == 'EXP-SERVGO-001'
                assert experiment['status'] == 'draft'
                assert experiment['observation_window_days'] == 14
                assert experiment['secondary_signals'] == ['positive_replies', 'demo_requests']
                assert client.post(f'/products/{pid}/experiments', json=experiment_body).status_code == 409
                invalid = dict(experiment_body, experiment_key='bad-key')
                assert client.post(f'/products/{pid}/experiments', json=invalid).status_code == 422
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
