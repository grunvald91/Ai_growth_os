"""The v4 design review cannot alter normal Studio pages or expose an anonymous preview."""
from fastapi.testclient import TestClient

from app.api.content_studio_auth import require_studio_auth, require_studio_csrf
from app.main import app


def test_growth_v4_preview_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("GROWTH_DESIGN_PREVIEW_ENABLED", raising=False)
    app.dependency_overrides[require_studio_auth] = lambda: None
    app.dependency_overrides[require_studio_csrf] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get("/studio/design-preview")
            assert response.status_code == 404
    finally:
        app.dependency_overrides.pop(require_studio_auth, None)
        app.dependency_overrides.pop(require_studio_csrf, None)


def test_growth_v4_preview_is_read_only_and_opt_in(monkeypatch):
    monkeypatch.setenv("GROWTH_DESIGN_PREVIEW_ENABLED", "1")
    app.dependency_overrides[require_studio_auth] = lambda: None
    app.dependency_overrides[require_studio_csrf] = lambda: None
    try:
        with TestClient(app) as client:
            response = client.get("/studio/design-preview")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["x-robots-tag"] == "noindex, nofollow"
            assert "Главная" in response.text
            assert "Простое видео" in response.text
            assert "SEO-завод" in response.text
            assert "Почта" in response.text
    finally:
        app.dependency_overrides.pop(require_studio_auth, None)
        app.dependency_overrides.pop(require_studio_csrf, None)


def test_growth_v4_preview_uses_real_studio_auth(monkeypatch):
    from app.core.config import Settings, get_settings

    monkeypatch.setenv("GROWTH_DESIGN_PREVIEW_ENABLED", "1")
    settings = Settings(
        app_env="production", studio_admin_username="preview-owner",
        studio_admin_password="local-test-fixture", _env_file=None,
    )
    monkeypatch.setitem(app.dependency_overrides, get_settings, lambda: settings)
    with TestClient(app) as client:
        url = "/studio/design-preview"
        assert client.get(url).status_code == 401
        browser = client.get(url, headers={"Accept": "text/html"}, follow_redirects=False)
        assert browser.status_code == 303
        assert browser.headers["location"].startswith("/studio/login?")
        assert client.get(url, auth=("preview-owner", "wrong")).status_code == 401
        authorized = client.get(url, auth=("preview-owner", "local-test-fixture"))
        assert authorized.status_code == 200
        assert authorized.headers["cache-control"] == "private, no-store"
