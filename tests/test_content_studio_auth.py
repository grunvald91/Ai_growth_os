"""Growth Studio security tests: owner-only staging access, fail closed in production."""
from fastapi.testclient import TestClient

from app.api.content_studio_auth import require_studio_auth
from app.core.config import Settings, get_settings
from app.main import app


def set_settings(settings):
    app.dependency_overrides[get_settings] = lambda: settings


def test_studio_requires_explicit_owner_credentials_in_production():
    set_settings(Settings(
        app_env="production",
        studio_admin_username="owner",
        studio_admin_password="correct-secret",
        _env_file=None,
    ))
    try:
        with TestClient(app) as client:
            url = "/studio/assets/studio.css"
            missing = client.get(url)
            assert missing.status_code == 401
            assert missing.headers["www-authenticate"] == "Basic"

            incorrect = client.get(url, auth=("owner", "wrong-secret"))
            assert incorrect.status_code == 401

            correct = client.get(url, auth=("owner", "correct-secret"))
            assert correct.status_code == 200
            assert "text/css" in correct.headers["content-type"]

            # Mutation endpoints must be guarded, not just GET pages.
            post = client.post(
                "/studio/products/00000000-0000-0000-0000-000000000000/"
                "plans/00000000-0000-0000-0000-000000000000/approve",
                follow_redirects=False,
            )
            assert post.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_studio_fails_closed_if_production_owner_secrets_missing():
    set_settings(Settings(
        app_env="production",
        studio_admin_username=None,
        studio_admin_password=None,
        _env_file=None,
    ))
    try:
        with TestClient(app) as client:
            assert client.get("/studio").status_code == 503
            assert client.get("/studio/assets/studio.css").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_studio_dev_access_allowed_only_when_no_credentials_configured():
    set_settings(Settings(
        app_env="development", studio_admin_username=None,
        studio_admin_password=None, _env_file=None,
    ))
    try:
        with TestClient(app) as client:
            assert client.get("/studio/assets/studio.js").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_partially_configured_owner_auth_is_not_silently_ignored():
    set_settings(Settings(
        app_env="development", studio_admin_username="owner",
        studio_admin_password=None, _env_file=None,
    ))
    try:
        with TestClient(app) as client:
            assert client.get("/studio/assets/studio.css").status_code == 503
    finally:
        app.dependency_overrides.clear()
