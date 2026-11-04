"""Regression checks for trusted Yandex selection and intake UI."""
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.content_studio_company import _collect_company_form, _ingest_yandex_selection
from app.integrations import yandex_evidence
from app.intake.contracts import IntakeError


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
    monkeypatch.setattr(yandex_evidence, "get_settings", lambda: SimpleNamespace(
        studio_session_secret="test-signed-studio-secret", servgo_internal_key=None,
        app_env="production"))


def example():
    return {"id": "12345", "name": "Пример", "address": "Сочи, Морская 1",
            "website": "https://example.org", "categories": ["Автосервис"],
            "phones": [], "maps_url": "https://yandex.ru/maps/org/12345"}


def test_signature_valid_and_bound_to_product_and_query():
    item = example()
    token = yandex_evidence.sign_organization(item, scope="company-1", query="автосервис", city="Сочи")
    assert yandex_evidence.verify_organization(token, scope="company-1",
                  query="автосервис", city="Сочи")["name"] == "Пример"
    with pytest.raises(ValueError):
        yandex_evidence.verify_organization(token, scope="company-2")
    with pytest.raises(ValueError):
        yandex_evidence.verify_organization(token, scope="company-1",
                                           query="автосалон")


def test_invalid_and_expired_tokens_are_rejected(monkeypatch):
    monkeypatch.setattr(yandex_evidence.time, "time", lambda: 100000)
    token = yandex_evidence.sign_organization(example(), scope="new", query="салоны", city="Сочи")
    raw, signature = token.split(".")
    with pytest.raises(ValueError):
        yandex_evidence.verify_organization(raw + "." + "x" * len(signature), scope="new")
    monkeypatch.setattr(yandex_evidence.time, "time", lambda: 102000)
    with pytest.raises(ValueError):
        yandex_evidence.verify_organization(token, scope="new")


def test_intake_writes_only_signed_evidence():
    product_id = uuid4()
    token = yandex_evidence.sign_organization(example(), scope="new",
                                               query="автосервис", city="Сочи")
    captured = []
    fake = SimpleNamespace(add_source=lambda product_id, session_id, source, **kw:
                           captured.append(source))
    _ingest_yandex_selection(fake, product_id, uuid4(), token, scope="new")
    assert len(captured) == 1
    assert captured[0].source_type == "public_third_party"
    assert "Морская 1" in captured[0].text
    with pytest.raises(IntakeError):
        _ingest_yandex_selection(fake, product_id, uuid4(),
                                 '{"id":"12345","name":"Поддельная компания"}', scope="new")
    assert len(captured) == 1


def test_existing_company_profile_loads_lookup_script():
    item = SimpleNamespace(id=uuid4(), name="Автосервис", description="Тест")
    rendered = _collect_company_form(item, "ИНН")
    assert 'data-ym-company-form' in rendered
    assert '<script src="/studio/assets/yandex-company.js" defer></script>' in rendered
