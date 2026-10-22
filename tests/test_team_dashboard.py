"""Owner-only dashboard: no paid inference, no public policy changes, durable policy reload."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.composition import build_llm_provider
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.llm.model_router import ModelRoutingProvider
from app.llm.router_store import RouterConfig, RouterConflict, RouterUpdate, read_state, save_state
from app.main import app
from app.models.domain import AgentRun, CostRecord, Product, Task


@pytest.fixture
def dashboard(tmp_path):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        products_root=tmp_path,
        model_router_admin_token="owner-secret-" + "x" * 36,
        xai_api_key="test-placeholder",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client, settings, db
    app.dependency_overrides.clear()
    db.close()
    Base.metadata.drop_all(engine)
    engine.dispose()


def auth(settings):
    return {"Authorization": "Bearer " + settings.model_router_admin_token}


def test_static_team_page_is_not_the_auth_boundary(dashboard):
    client, settings, _ = dashboard
    response = client.get("/studio/team")
    assert response.status_code == 200
    assert "ИИ-командой" in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert client.get("/studio/team/assets/team.js").status_code == 200
    assert client.get("/studio/team/assets/team.css").status_code == 200
    assert client.get("/api/admin/team").status_code == 401
    assert client.get("/api/admin/team", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/api/admin/team/preview", json={"enabled": False, "profiles": []}).status_code == 401
    assert client.get("/api/admin/team/catalog").status_code == 401


def test_dashboard_requires_configured_long_secret(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path, model_router_admin_token="too-short")
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        assert client.get("/api/admin/team", headers={"Authorization": "Bearer too-short"}).status_code == 503
    app.dependency_overrides.clear()


def test_owner_can_preview_and_save_safe_policy_with_revision(dashboard):
    client, settings, _ = dashboard
    response = client.get("/api/admin/team", headers=auth(settings))
    assert response.status_code == 200
    data = response.json()
    assert data["revision"] == 0
    assert len(data["preview"]["roles"]) == 39
    assert not data["preview"]["ready"]  # CMO premium model disabled initially.
    assert data["config"]["enabled"] is False
    assert "xai_api_key" not in response.text
    config = data["config"]
    for profile in config["profiles"]:
        if profile["model"] == "claude-opus-5-5":
            profile["enabled"] = True
    config["enabled"] = True
    config["overrides"] = {
        "company_intake": ["grok-4.7"],
        "cmo": ["claude-opus-5-5"],
        "article_writer:critic": ["grok-4.7"],
    }
    result = client.post("/api/admin/team/preview", json=config, headers=auth(settings))
    assert result.status_code == 200
    assert result.json()["ready"] is True
    assert result.json()["critics_active"] is False
    saved = client.put("/api/admin/team", json={
        "expected_revision": 0, "config": config,
    }, headers=auth(settings))
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    assert read_state(settings).revision == 1
    assert read_state(settings).config.enabled is True
    provider = build_llm_provider(settings)
    assert isinstance(provider, ModelRoutingProvider)
    from app.llm.schemas import LLMMessage, LLMRequest
    choice = provider.router.select(LLMRequest(
        messages=[LLMMessage(role="user", content="strategy")],
        metadata={"team_role": "cmo"},
    ))
    assert choice.candidates == ("claude-opus-5-5",)
    stale = client.put("/api/admin/team", json={
        "expected_revision": 0, "config": config,
    }, headers=auth(settings))
    assert stale.status_code == 409


def test_dashboard_accepts_any_enabled_model_for_any_role_but_not_an_unknown_one(dashboard):
    client, settings, _ = dashboard
    data = client.get("/api/admin/team", headers=auth(settings)).json()
    config = data["config"]
    config["overrides"] = {"cmo": ["kimi-2.7"], "cmo:critic": ["kimi-2.7", "grok-4.7"]}
    ok = client.put("/api/admin/team", json={"expected_revision": 0, "config": config}, headers=auth(settings))
    assert ok.status_code == 200 and ok.json()["config"]["overrides"]["cmo"] == ["kimi-2.7"]
    config["overrides"] = {"cmo": ["not-in-the-catalog"]}
    bad = client.put("/api/admin/team", json={"expected_revision": 1, "config": config}, headers=auth(settings))
    assert bad.status_code == 422


def test_dashboard_refuses_to_enable_no_premium_model(dashboard):
    client, settings, _ = dashboard
    config = client.get("/api/admin/team", headers=auth(settings)).json()["config"]
    config["enabled"] = True
    response = client.put("/api/admin/team", json={
        "expected_revision": 0, "config": config,
    }, headers=auth(settings))
    assert response.status_code == 422


def test_dashboard_usage_scoped_to_product_and_owner(dashboard):
    client, settings, db = dashboard
    a = Product(name="ServGo", slug="servgo")
    b = Product(name="Other", slug="other")
    db.add_all([a, b])
    db.flush()
    task = Task(product_id=a.id, title="Review", assigned_agent="cmo")
    db.add(task)
    db.flush()
    run = AgentRun(task_id=task.id, agent_name="cmo", status="completed")
    db.add(run)
    db.flush()
    from datetime import datetime, timezone
    db.add(CostRecord(
        product_id=a.id, agent_run_id=run.id, category="llm", provider="xai",
        amount=0, currency="USD", input_tokens=1000, output_tokens=500,
        incurred_at=datetime.now(timezone.utc),
        details={"cost_known": False, "estimated_amount_usd": "0.003"},
    ))
    db.commit()
    url = "/api/admin/team/usage?product_id=" + str(a.id)
    assert client.get(url).status_code == 401
    payload = client.get(url, headers=auth(settings)).json()
    assert payload["total_calls"] == 1
    assert payload["estimated_usd"] == "0.003"
    assert payload["by_role"]["cmo"]["input_tokens"] == 1000
    other = client.get(
        "/api/admin/team/usage?product_id=" + str(b.id), headers=auth(settings),
    ).json()
    assert other["total_calls"] == 0
    assert client.get(
        "/api/admin/team/usage?product_id=" + str(uuid4()), headers=auth(settings),
    ).status_code == 404


def test_atomic_config_save_cannot_be_overwritten_by_stale_version(tmp_path):
    settings = Settings(_env_file=None, products_root=tmp_path)
    config = read_state(settings).config
    saved = save_state(settings, RouterUpdate(expected_revision=0, config=config))
    assert saved.revision == 1
    with pytest.raises(RouterConflict):
        save_state(settings, RouterUpdate(expected_revision=0, config=config))
    assert (tmp_path / ".model-router-config.json").stat().st_mode & 0o777 == 0o600


def test_page_api_works_with_studio_login_without_owner_token(tmp_path):
    # Regression: the page called /api/admin/team, where browsers do not resend
    # the Studio login cached for /studio/; production has no owner token, so
    # loading failed with 503 and every button then hit an empty state.
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None, app_env="production", products_root=tmp_path,
        studio_admin_username="owner", studio_admin_password="studio-secret",
        studio_public_origin="https://growth.servgo.ru",
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db
    login = ("owner", "studio-secret")
    origin = {"Origin": "https://growth.servgo.ru"}
    try:
        with TestClient(app) as client:
            anonymous = client.get("/studio/team/api")
            assert anonymous.status_code == 401
            assert anonymous.headers["www-authenticate"] == "Basic"

            loaded = client.get("/studio/team/api", auth=login)
            assert loaded.status_code == 200, loaded.text
            data = loaded.json()
            assert data["config"]["profiles"] and data["revision"] == 0

            preview = client.post("/studio/team/api/preview", auth=login, headers=origin, json=data["config"])
            assert preview.status_code == 200 and "issues" in preview.json()
            saved = client.put("/studio/team/api", auth=login, headers=origin,
                               json={"expected_revision": 0, "config": data["config"]})
            assert saved.status_code == 200 and saved.json()["revision"] == 1
            assert client.put("/studio/team/api", auth=login, json={
                "expected_revision": 1, "config": data["config"]}).status_code == 403  # no Origin
    finally:
        app.dependency_overrides.clear()
        db.close()
        engine.dispose()


def test_page_script_calls_the_studio_scoped_api():
    from pathlib import Path
    script = (Path(__file__).parents[1] / "app/api/team_dashboard_assets/team.js").read_text(encoding="utf-8")
    assert "var API='/studio/team/api';" in script
    assert "/api/admin/" not in script
    # A failed load must keep the buttons off instead of reading an empty state.
    assert "setReady(false)" in script and "retryButton" in script


def test_catalog_lists_every_gateway_model_for_selection(dashboard, monkeypatch):
    client, settings, _ = dashboard
    async def fake(**_):
        return tuple(f"model-{i}" for i in range(60)) + ("kimi-k3", "gemini-3.1-flash-image")
    monkeypatch.setattr("app.api.team_dashboard.list_gateway_models", fake)
    data = client.get("/api/admin/team/catalog", headers=auth(settings)).json()
    assert data["listed_count"] == 62 and len(data["gateway_models"]) == 62
    assert len(data["unconfigured_ids"]) >= 60  # no 100-item cut-off any more
    kinds = {row["model"]: row for row in data["gateway_models"]}
    assert kinds["kimi-k3"]["configured"] and kinds["gemini-3.1-flash-image"]["text"] is False


def test_catalog_can_hold_more_than_forty_models_and_roles_cover_the_real_workflows(dashboard):
    client, settings, _ = dashboard
    config = client.get("/api/admin/team", headers=auth(settings)).json()["config"]
    config["profiles"] += [{"model": f"extra-{i}", "tier": "balanced", "enabled": True} for i in range(70)]
    config["overrides"] = {"post_rewriter": ["extra-1", "extra-2", "extra-3"],
                           "whiteboard_storyboard": ["extra-4", "extra-5"],
                           "post_rewriter:critic": ["extra-6"]}
    saved = client.put("/api/admin/team", json={"expected_revision": 0, "config": config}, headers=auth(settings))
    assert saved.status_code == 200, saved.text
    ids = {role["id"] for role in saved.json()["preview"]["roles"]}
    assert {"post_strategist", "post_writer_editor", "post_rewriter", "creative_strategist", "editorial_planner",
            "competitive_intelligence", "whiteboard_storyboard"} <= ids
    from app.content.whiteboard.runtime import generation_profile, SingleCallStoryboardLLM
    # A new whiteboard job takes the model chosen on the team page; reserves follow it.
    wb = settings.model_copy(update={"whiteboard_storyboard_model": "kimi-2.7", "whiteboard_voice": "eve",
                                     "whiteboard_image_model": "x", "whiteboard_speech_model": "x",
                                     "whiteboard_regions_model": "x"})
    assert generation_profile(wb, "gemini_ru")[0].storyboard == "extra-4"
    assert generation_profile(wb, "configured")[0].storyboard == "extra-4"
    assert SingleCallStoryboardLLM(wb).fallback_models("extra-4")[0] == "extra-5"


def test_page_offers_catalog_models_reserves_and_a_bottom_save_bar_on_phones():
    from pathlib import Path
    base = Path("app/api/team_dashboard_assets")
    script, css, page = (base / "team.js").read_text(), (base / "team.css").read_text(), (base / "index.html").read_text()
    assert "gateway_models" in script and "ensureProfile" in script and "Резервная 2" in script
    assert "catalogAdd" in page and "position:fixed" in css.split("@media(max-width:700px){")[-1]


def test_profiles_without_an_enabled_field_are_shown_as_enabled_with_full_fields(dashboard):
    client, settings, _ = dashboard
    # The built-in default catalog omits "enabled" for models that are on; the page must see them as on.
    data = client.get("/api/admin/team", headers=auth(settings)).json()
    by_model = {p["model"]: p for p in data["config"]["profiles"]}
    assert by_model["kimi-2.7"]["enabled"] is True and by_model["kimi-k3"]["enabled"] is False
    assert all({"enabled", "priority", "web_search_verified", "input_usd_per_million"} <= set(p) for p in by_model.values())
    config = {"enabled": False, "profiles": [{"model": "a-model", "tier": "premium"}], "overrides": {}}
    pre = client.post("/api/admin/team/preview", json=config, headers=auth(settings))
    assert pre.status_code == 200
