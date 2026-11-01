"""Full browser-form regression: 60-second whiteboard under nginx TLS termination.

Creates an approved queued job in an isolated SQLite fixture but NEVER calls
a provider or executes a worker. Verifies both form and confirmation POSTs.
"""
from uuid import UUID

from fastapi.testclient import TestClient

from app.api import whiteboard as whiteboard_api
from app.core.config import get_settings
from app.main import app
from app.models.domain import TaskStatus
from test_whiteboard_jobs import environment  # noqa: F401


def test_mobile_60_second_whiteboard_form_and_confirmation_behind_nginx(environment, monkeypatch):
    factory, settings, product_id, _ = environment
    production = settings.model_copy(update={
        "studio_public_origin": "https://growth.servgo.ru",
        "studio_servgo_gateway_enabled": True,
    })
    app.dependency_overrides[get_settings] = lambda: production

    async def no_network_preflight(*args, **kwargs):
        return None
    monkeypatch.setattr(whiteboard_api, "preflight", no_network_preflight)

    # ASGI only sees the *internal HTTP hop*, while the public browser uses HTTPS.
    with TestClient(app, base_url="http://growth.servgo.ru", headers={
        "Origin": "https://growth.servgo.ru",
        "X-Forwarded-Proto": "https",
        "Sec-Fetch-Site": "same-origin",
    }) as browser:
        browser.auth = ("owner", "test-owner-password")
        url = f"/studio/products/{product_id}/whiteboard"
        page = browser.get(url)
        assert page.status_code == 200
        assert "name=\"duration_seconds\"" in page.text

        created = browser.post(url, data={
            "topic": "Почему горячее тело массивнее холодного и как это объяснить?",
            "duration_seconds": "60",
            "max_api_calls": "auto",
            "visual_review": "off",
            "science_mode": "auto",
        }, follow_redirects=False)
        assert created.status_code == 303
        task_id = UUID(created.headers["location"].rsplit("/", 1)[-1])

        with factory() as db:
            state = whiteboard_api.job_state(db, product_id, task_id)
            assert state["status"] == TaskStatus.WAITING_APPROVAL.value
            assert state["request"]["duration_seconds"] == 60

        # This is the *second POST* behind the button "Подтвердить генерацию".
        confirmed = browser.post(f"{url}/{task_id}/approve", data={
            "request_digest": state["request_digest"],
            "confirmed": "yes",
        }, follow_redirects=False)
        assert confirmed.status_code == 303
        with factory() as db:
            state = whiteboard_api.job_state(db, product_id, task_id)
            assert state["status"] == TaskStatus.QUEUED.value
            assert state["approval_status"] == "approved"

        bad = browser.post(url, data={
            "topic": "Попытка межсайтовой отправки через форму",
            "duration_seconds": "60",
        }, headers={"Origin": "https://evil.example"}, follow_redirects=False)
        assert bad.status_code == 403
