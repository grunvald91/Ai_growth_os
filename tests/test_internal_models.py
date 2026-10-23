"""What the ServGo mail asks the studio: the mail agent's model and the company it searches clients for."""
import json
from datetime import date, timedelta

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import internal_models
from app.api.team_dashboard import preview_configuration, role_descriptors
from app.core.config import Settings, get_settings
from app.llm.router_store import RouterConfig, RouterUpdate, default_configuration, read_state, save_state
from app.main import app
from app.models.domain import Product
from app.sso import access
from app.sso.models import StudioBusinessLink

KEY = "k" * 40


def make_settings(tmp_path, **extra):
    return Settings(_env_file=None, products_root=tmp_path, servgo_internal_key=KEY, xai_api_key="test-placeholder",
                    servgo_api_url="https://servgo.test/api", **extra)


@pytest.fixture
def client_and_settings(tmp_path):
    settings = make_settings(tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, settings
    app.dependency_overrides.clear()


def save(settings, *, enabled=True, overrides=None):
    state = read_state(settings)
    base = default_configuration(settings)
    config = RouterConfig(enabled=enabled, profiles=base.profiles, overrides=overrides or {},
                          max_call_usd=base.max_call_usd)
    save_state(settings, RouterUpdate(expected_revision=state.revision, config=config))


def test_mail_agent_is_listed_on_the_team_page_and_routable():
    roles = {role["id"]: role for role in role_descriptors()}
    assert "mail_agent" in roles
    assert "почт" in roles["mail_agent"]["name"].lower()
    config = default_configuration(Settings(_env_file=None, xai_api_key="test-placeholder"))
    rows = {row["id"]: row for row in preview_configuration(config)["roles"]}
    assert rows["mail_agent"]["main"]["tier"] == "balanced"


def test_owner_pick_is_returned_with_the_shared_key(client_and_settings):
    client, settings = client_and_settings
    model = next(p["model"] for p in default_configuration(settings).profiles if p.get("enabled", True))
    save(settings, overrides={"mail_agent": [model]})
    response = client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": KEY})
    assert response.status_code == 200
    assert response.json() == {"role": "mail_agent", "models": [model]}


def test_without_a_pick_the_mail_gets_the_route_the_team_page_shows(client_and_settings):
    client, settings = client_and_settings
    save(settings)
    from app.api.team_dashboard import _decision
    from app.llm.router_store import validate_configuration
    shown = _decision(validate_configuration(read_state(settings).config), "mail_agent", "main")["models"]
    answer = client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": KEY}).json()["models"]
    assert answer == shown and answer


def test_router_switched_off_means_the_mail_keeps_its_own_model(client_and_settings):
    client, settings = client_and_settings
    save(settings, enabled=False, overrides={})
    assert client.get("/studio/internal/model/mail_agent", headers={"X-Studio-Key": KEY}).json()["models"] == []


def test_wrong_or_missing_key_is_refused(client_and_settings):
    client, _ = client_and_settings
    for path in ("/studio/internal/model/mail_agent", "/studio/internal/companies",
                 "/studio/internal/companies/00000000-0000-0000-0000-000000000000/brief"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Studio-Key": "x" * 40}).status_code == 401


def test_key_not_configured_refuses_everything(tmp_path):
    with pytest.raises(HTTPException) as caught:
        internal_models.require_servgo("", Settings(_env_file=None, products_root=tmp_path))
    assert caught.value.status_code == 401


def test_only_shared_roles_are_exposed(client_and_settings):
    client, _ = client_and_settings
    assert client.get("/studio/internal/model/cmo", headers={"X-Studio-Key": KEY}).status_code == 404


PROFILE = {
    "schema_version": "1.0",
    "identity": {"name": "Салон Мята", "one_liner": "Студия маникюра и бровей в Сочи", "category": "Красота",
                 "website": "https://mint.example", "service_geography": ["Сочи"]},
    "offer": {"description": "Маникюр, брови, ресницы", "products_services": [
        {"name": "Маникюр", "description": "Классический и аппаратный"}],
        "primary_conversion_action": "Записаться онлайн"},
    "customers": {"primary_segments": ["Женщины 25-45"], "ideal_customer": "Работающая женщина рядом со студией",
                  "exclusions": ["Оптовики"], "pains": ["Нет времени ждать"]},
    "proof": {"approved_facts": [{"statement": "Работаем с 2019 года"}]},
}


def test_companies_are_listed_for_the_mail(db, tmp_path):
    db.add_all([Product(name="ServGo", slug="servgo"), Product(name="Мята", slug="mint")])
    db.commit()
    rows = internal_models.companies(make_settings(tmp_path), db)
    assert [r["name"] for r in rows] == ["ServGo", "Мята"] and all(r["id"] for r in rows)


def test_brief_is_built_from_the_profile_and_the_booked_services(db, tmp_path):
    product = Product(name="Мята", slug="mint")
    db.add(product)
    db.commit()
    (tmp_path / "mint").mkdir()
    (tmp_path / "mint" / "company-profile.json").write_text(json.dumps(PROFILE, ensure_ascii=False), encoding="utf-8")
    db.add(StudioBusinessLink(servgo_business_id=7, product_id=product.id))
    db.commit()
    today = date.today()

    def platform(request):
        assert request.headers["x-studio-key"] == KEY
        def b(i, service, status="confirmed"):
            return {"id": i, "date": today.isoformat(), "time": "10:00", "status": status, "service": service,
                    "client_name": "Анна", "client_phone": "+79990000001"}
        return httpx.Response(200, json={"today": today.isoformat(), "bookings": [
            b(1, "Маникюр"), b(2, "Маникюр"), b(3, "Брови"), b(4, "Брови", "cancelled")]})

    access._transport = httpx.MockTransport(platform)
    try:
        brief = internal_models.brief(product.id, make_settings(tmp_path), db)
    finally:
        access._transport = None
    assert brief["name"] == "Салон Мята" and brief["about"] == "Студия маникюра и бровей в Сочи"
    assert brief["geography"] == ["Сочи"] and brief["website"] == "https://mint.example"
    assert brief["services"] == [{"name": "Маникюр", "description": "Классический и аппаратный"}]
    assert brief["ideal_customer"] == "Работающая женщина рядом со студией"
    assert brief["not_our_customers"] == ["Оптовики"] and brief["verified_facts"] == ["Работаем с 2019 года"]
    assert brief["booked_services"] == [{"service": "Маникюр", "bookings": 2}, {"service": "Брови", "bookings": 1}]
    assert "Анна" not in json.dumps(brief, ensure_ascii=False) and "+7999" not in json.dumps(brief)


def test_brief_without_profile_or_booking_link_still_names_the_company(db, tmp_path):
    product = Product(name="Новая компания", slug="fresh", description="Аренда яхт")
    db.add(product)
    db.commit()
    brief = internal_models.brief(product.id, make_settings(tmp_path), db)
    assert brief["name"] == "Новая компания" and brief["about"] == "Аренда яхт"
    assert brief["booked_services"] == [] and brief["geography"] == [] and brief["profile_source"] == "missing"


def test_platform_trouble_does_not_break_the_brief(db, tmp_path):
    product = Product(name="Мята", slug="mint2")
    db.add(product)
    db.commit()
    db.add(StudioBusinessLink(servgo_business_id=8, product_id=product.id))
    db.commit()
    access._transport = httpx.MockTransport(lambda request: httpx.Response(500, json={}))
    try:
        assert internal_models.brief(product.id, make_settings(tmp_path), db)["booked_services"] == []
    finally:
        access._transport = None


def test_unknown_company_is_404(db, tmp_path):
    from uuid import uuid4
    with pytest.raises(HTTPException) as caught:
        internal_models.brief(uuid4(), make_settings(tmp_path), db)
    assert caught.value.status_code == 404
