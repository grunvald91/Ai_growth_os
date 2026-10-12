"""Denied commands keep browser form data without executing business writes."""
import json
import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.main import app

URL = "/studio/products/04392041-ea09-4853-874e-8060bee7286c/create"
FIELDS = {"format": "package", "channel": "vk", "topic": "Тема <script>alert(1)</script>",
          "audience": "Родители & дети", "editorial_brief": "Тон: тёплый\nБез обещаний"}


@pytest.fixture
def client():
    settings = Settings(app_env="production", studio_admin_username="owner",
                        studio_admin_password="test-secret", studio_public_origin="https://growth.servgo.ru",
                        _env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    def blocked_db():
        raise AssertionError("Rejected command must not access business data")
    app.dependency_overrides[get_db] = blocked_db
    try:
        with TestClient(app) as client:
            client.auth = ("owner", "test-secret")
            yield client
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("origin", ["null", "https://foreign.example", "https://[broken"])
def test_browser_rejection_renders_form_and_preserves_fields(client, origin):
    response = client.post(URL, data=FIELDS, headers={"Origin": origin, "Accept": "text/html"})
    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert response.headers["cache-control"] == "no-store"
    assert "Не удалось отправить задание" in response.text
    assert "Тема &lt;script&gt;alert(1)&lt;/script&gt;" in response.text
    assert "Родители &amp; дети" in response.text
    assert "Тон: тёплый\nБез обещаний" in response.text
    assert 'value="package" checked' in response.text
    assert '<script>alert(1)</script>' not in response.text
    assert 'action="' + URL + '"' in response.text


def test_json_client_gets_structured_error_and_safe_diagnostics(client, caplog):
    response = client.post(URL, data=FIELDS, headers={"Origin": "null", "Accept": "application/json",
        "Referer": "https://user:password@growth.servgo.ru/private?token=secret",
        "Sec-Fetch-Site": "same-origin"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-origin action rejected"
    assert len(response.json()["error_id"]) == 12
    assert "password" not in caplog.text and "token=secret" not in caplog.text
    assert FIELDS["audience"] not in caplog.text
    diagnostic = next(r.getMessage().split("studio_origin_rejected ", 1)[1]
                      for r in caplog.records if "studio_origin_rejected " in r.getMessage())
    diagnostic = json.loads(diagnostic)
    assert diagnostic["origin"] == "null"
    assert diagnostic["referer_origin"] == "https://growth.servgo.ru"
    assert diagnostic["configured_origin"] == "https://growth.servgo.ru"
    assert diagnostic["error_id"] == response.json()["error_id"]


def test_cross_site_is_rejected_even_with_trusted_origin(client):
    response = client.post(URL, data=FIELDS, headers={"Origin": "https://growth.servgo.ru",
                                                     "Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Cross-site action rejected"


def test_origin_null_never_falls_back_to_trusted_referer(client):
    response = client.post(URL, data=FIELDS, headers={"Origin": "null", "Referer": "https://growth.servgo.ru/"})
    assert response.status_code == 403


def test_same_origin_and_legacy_referer_reach_route(client):
    class MissingProduct:
        def get(self, *args):
            return None
    app.dependency_overrides[get_db] = lambda: MissingProduct()
    for headers in ({"Origin": "https://growth.servgo.ru", "Sec-Fetch-Site": "same-origin"},
                    {"Referer": "https://growth.servgo.ru/studio"}):
        assert client.post(URL, data=FIELDS, headers=headers).status_code == 404


def test_unauthenticated_request_is_still_denied_before_form_rendering(client):
    client.auth = None
    response = client.post(URL, data=FIELDS, headers={"Origin": "null", "Accept": "text/html"})
    assert response.status_code == 401
    assert FIELDS["topic"] not in response.text
