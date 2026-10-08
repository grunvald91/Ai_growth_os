import base64
import copy
import json
import re
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import sales_agent
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Approval, Artifact, Product, ToolCall
from app.tools.servgo import ServGoAdapter

PASSWORD = 'synthetic-owner-password-123'
TOKEN = 'synthetic-platform-token-1234567890'
DOCUMENT = {
    'business_id': 7, 'business_name': 'Демо сервис', 'revision': 2,
    'policy': sales_agent.Policy().model_dump(),
    'business_rules': {'prepayment_enabled': True, 'prepayment_percent': 25, 'slot_interval_minutes': 45},
    'always_forbidden': ['Подтверждать получение денег'],
    'coverage': [{'rule': 'Сложный тариф', 'status': 'form_or_operator'}],
}


class FakeAdapter:
    document = None
    failure = None
    writes = []

    def businesses(self):
        return [{'id': 7, 'name': 'Демо сервис'}]

    def read(self, business_id):
        return copy.deepcopy(self.document)

    def update(self, business_id, data):
        self.writes.append((business_id, copy.deepcopy(data)))
        if self.failure:
            raise self.failure
        type(self).document = {**self.document, 'revision': self.document['revision'] + 1, 'policy': data['policy']}
        return copy.deepcopy(self.document)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv('SERVGO_SALES_OWNER_PASSWORD', PASSWORD)
    get_settings.cache_clear()
    monkeypatch.setattr(sales_agent, 'ServGoAdapter', FakeAdapter)
    FakeAdapter.document = copy.deepcopy(DOCUMENT)
    FakeAdapter.failure = None; FakeAdapter.writes = []
    engine = create_engine('sqlite+pysqlite:///:memory:', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        product = Product(name='ServGo', slug='servgo')
        other = Product(name='Другой проект', slug='other')
        db.add_all([product, other]); db.commit()
        def override():
            yield db
        app.dependency_overrides[get_db] = override
        with TestClient(app, base_url='https://testserver') as client:
            client.headers['Authorization'] = 'Basic ' + base64.b64encode(('owner:' + PASSWORD).encode()).decode()
            yield client, db, product, other
    app.dependency_overrides.pop(get_db, None)
    engine.dispose(); get_settings.cache_clear()


def page_and_change(client, product):
    path = f'/studio/products/{product.id}/sales-agent'
    response = client.get(path)
    assert response.status_code == 200
    csrf = re.search(r'data-csrf="([^"]+)"', response.text).group(1)
    data = {'expected_revision': 2, 'policy': sales_agent.Policy(booking_mode='operator_only').model_dump(), 'approved': True}
    return path + '/businesses/7', data, {'X-CSRF-Token': csrf}


def test_page_requires_owner_and_displays_live_business_rules(setup):
    client, db, product, other = setup
    response = client.get(f'/studio/products/{product.id}/sales-agent')
    assert 'ИИ-продавец' in response.text and 'Демо сервис' in response.text
    assert 'Предоплата, %' in response.text and 'Форма / оператор' in response.text
    assert TOKEN not in response.text and PASSWORD not in response.text
    assert response.headers['cache-control'] == 'no-store'
    assert client.get(f'/studio/products/{other.id}/sales-agent').status_code == 404
    del client.headers['Authorization']
    assert client.get(f'/studio/products/{product.id}/sales-agent').status_code == 401


def test_save_applies_exact_policy_and_records_manual_approval_and_tool_call(setup):
    client, db, product, _ = setup
    path, data, headers = page_and_change(client, product)
    response = client.put(path, json=data, headers=headers)
    assert response.status_code == 200 and response.json()['revision'] == 3
    assert FakeAdapter.writes == [(7, {key: value for key, value in data.items() if key != 'approved'})]
    approval = db.scalar(select(Approval)); artifact = db.scalar(select(Artifact)); call = db.scalar(select(ToolCall))
    assert approval.status == 'approved' and approval.risk_level == 'RED'
    assert approval.decided_by == 'local_owner' and approval.artifact_id == artifact.id
    assert call.approval_id == approval.id and call.status == 'succeeded'
    assert json.loads(artifact.content)['before']['revision'] == 2
    assert PASSWORD not in artifact.content and TOKEN not in json.dumps(call.request_data)
    replay = client.put(path, json=data, headers=headers)
    assert replay.status_code == 409 and len(FakeAdapter.writes) == 1


def test_csrf_scope_and_explicit_approval_are_required(setup):
    client, db, product, _ = setup
    path, data, headers = page_and_change(client, product)
    assert client.put(path, json=data).status_code == 403
    assert client.put(path, json=data, headers={**headers, 'Origin': 'https://other.invalid'}).status_code == 403
    assert client.put(path.replace('/7', '/8'), json=data, headers=headers).status_code == 403
    assert client.put(path, json={**data, 'approved': False}, headers=headers).status_code == 422
    assert client.put(path, json={**data, 'policy': {**data['policy'], 'confirm_payments': True}}, headers=headers).status_code == 422
    assert not FakeAdapter.writes and db.scalar(select(ToolCall)) is None


def test_rejected_external_change_is_audited_without_success_claim(setup):
    client, db, product, _ = setup
    path, data, headers = page_and_change(client, product)
    FakeAdapter.failure = HTTPException(409, 'Настройки изменены в ServGo')
    response = client.put(path, json=data, headers=headers)
    assert response.status_code == 409
    call = db.scalar(select(ToolCall))
    assert call.status == 'failed' and call.response_data is None
    assert FakeAdapter.document['revision'] == 2


def test_missing_connection_has_no_save_form(setup, monkeypatch):
    client, db, product, _ = setup
    monkeypatch.setattr(sales_agent, 'ServGoAdapter', ServGoAdapter)
    monkeypatch.delenv('SERVGO_API_BASE_URL', raising=False)
    monkeypatch.delenv('SERVGO_PLATFORM_API_TOKEN', raising=False)
    get_settings.cache_clear()
    response = client.get(f'/studio/products/{product.id}/sales-agent')
    assert 'Сохранение недоступно' in response.text
    assert 'id="sales-settings"' not in response.text


def test_adapter_keeps_token_server_side_and_sanitizes_upstream_errors(monkeypatch):
    monkeypatch.setenv('SERVGO_API_BASE_URL', 'https://servgo.test/api')
    monkeypatch.setenv('SERVGO_PLATFORM_API_TOKEN', TOKEN)
    get_settings.cache_clear()
    seen = []
    def upstream(request):
        seen.append(request)
        if request.method == 'GET':
            return httpx.Response(200, json=[{'id': 7, 'name': 'Демо сервис'}])
        return httpx.Response(500, text=TOKEN + ' internal error')
    real_client = httpx.Client
    with patch('app.tools.servgo.httpx.Client', side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs)):
        adapter = ServGoAdapter()
        assert adapter.businesses()[0]['id'] == 7
        with pytest.raises(HTTPException) as exc:
            adapter.update(7, {'policy': {}, 'expected_revision': 2})
        assert TOKEN not in exc.value.detail
    assert seen[0].headers['Authorization'] == 'Bearer ' + TOKEN
    assert str(seen[0].url) == 'https://servgo.test/api/platform/sales/businesses'
    get_settings.cache_clear()
