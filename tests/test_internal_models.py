"""The ServGo mail asks which model the owner picked for it on the team page."""
import json

import pytest
from fastapi.testclient import TestClient

from app.api.team_dashboard import preview_configuration, role_descriptors
from app.core.config import Settings, get_settings
from app.llm.router_store import RouterConfig, RouterUpdate, default_configuration, read_state, save_state
from app.main import app

KEY = "k" * 40


@pytest.fixture
def client_and_settings(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path, servgo_internal_key=KEY, xai_api_key="test-placeholder")
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings
    app.dependency_overrides.clear()


def choose(settings, role, models):
    state = read_state(settings)
    config = default_configuration(settings)
    config = RouterConfig(enabled=True, profiles=config.profiles, overrides={role: models},
                          max_call_usd=config.max_call_usd)
    save_state(settings, RouterUpdate(expected_revision=state.revision, config=config))


def test_mail_agent_is_listed_on_the_team_page_and_routable():
    roles = {role["id"]: role for role in role_descriptors()}
    assert "mail_agent" in roles
    assert "почт" in roles["mail_agent"]["name"].lower()
    config = default_configuration(Settings(_env_file=None, xai_api_key="test-placeholder"))
    rows = {row["id"]: row for row in preview_configuration(config)["roles"]}
    assert rows["mail_agent"]["main"]["tier"] == "balanced"


def test_chosen_model_is_returned_with_the_shared_key(client_and_settings):
    client, settings = client_and_settings
    profiles = default_configuration(settings).profiles
    model = next(item["model"] for item in profiles if item.get("enabled", True))
    choose(settings, "mail_agent", [model])
    response = client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": KEY})
    assert response.status_code == 200
    assert response.json() == {"role": "mail_agent", "models": [model]}


def test_no_choice_means_empty_list_so_the_mail_keeps_its_own_default(client_and_settings):
    client, _ = client_and_settings
    response = client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": KEY})
    assert response.status_code == 200
    assert response.json()["models"] == []


def test_wrong_or_missing_key_is_refused(client_and_settings):
    client, _ = client_and_settings
    assert client.get("/studio/internal/model/mail_agent").status_code == 401
    assert client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": "x" * 40}).status_code == 401


def test_key_not_configured_refuses_everything(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path, xai_api_key="test-placeholder")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            assert client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": ""}).status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_only_shared_roles_are_exposed(client_and_settings):
    client, _ = client_and_settings
    assert client.get("/studio/internal/model/cmo", headers={"X-Studio-Key": KEY}).status_code == 404
    assert "models" not in json.dumps(client.get("/studio/internal/model/cmo", headers={"X-Studio-Key": KEY}).json())
