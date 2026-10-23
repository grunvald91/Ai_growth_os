"""Home page and dispatcher endpoints: sign-in, origin checks, company scope, page contents."""
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.dispatcher import get_dispatcher_llm
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.dispatcher.models import DispatcherMessage
from app.llm.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse
from app.llm.service import LLMService
from app.main import app
from app.models.domain import Product, Task
from app.privacy import PrivacyGateway


@pytest.fixture
def env(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    servgo, other = Product(name="ServGo", slug="servgo"), Product(name="Салон", slug="salon")
    db.add_all([servgo, other])
    db.commit()
    settings = Settings(_env_file=None, products_root=tmp_path, whiteboard_image_model="google/image")
    provider = FakeLLMProvider(LLMResponse(content=json.dumps({
        "intent": "execute", "capability": "create_post", "confidence": 0.9,
        "params": {"topic": "Как снизить неявки"}}), model="fake"))

    def override_db():
        yield db
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_dispatcher_llm] = lambda: LLMService(
        privacy_gateway=PrivacyGateway(), provider=provider, critic_enabled=False)
    yield db, settings, servgo, other, provider
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def chip_body(key="k1"):
    return {"mode": "chip", "client_key": key, "chip": {
        "label": "Что в работе", "capability": "show_work", "params": {}}}


def test_home_replaces_the_overview_only_for_enabled_companies(env):
    db, settings, servgo, other, _ = env
    client = TestClient(app)
    page = client.get(f"/studio/products/{servgo.id}")
    assert page.status_code == 200
    assert 'id="dp-form"' in page.text and "dispatcher.js" in page.text and "Чем помочь?" in page.text
    # Old overview is still reachable and still used by companies without the dispatcher.
    assert "dp-form" not in client.get(f"/studio/products/{other.id}").text
    assert "dp-form" not in client.get(f"/studio/products/{servgo.id}/classic").text


def test_home_embeds_state_safely_and_shows_hints(env):
    db, settings, servgo, _, _ = env
    page = TestClient(app).get(f"/studio/products/{servgo.id}").text
    raw = re.search(r'<script type="application/json" id="dp-state">(.*?)</script>', page, re.S).group(1)
    state = json.loads(raw)
    labels = [chip["label"] for chip in state["hints"]]
    assert "Что в работе" in labels and "Составить контент-план" in labels
    assert state["messages"] == []


def test_user_text_cannot_break_out_of_the_embedded_state(env):
    _, _, servgo, _, _ = env
    client = TestClient(app)
    hostile = "</script><img src=x onerror=alert(1)>"
    client.post(f"/studio/products/{servgo.id}/dispatch/messages", json={
        "mode": "chip", "client_key": "x", "chip": {"label": hostile, "capability": "show_work", "params": {}}})
    page = client.get(f"/studio/products/{servgo.id}").text
    assert "</script><img" not in page
    raw = re.search(r'<script type="application/json" id="dp-state">(.*?)</script>', page, re.S).group(1)
    assert hostile in [m["text"] for m in json.loads(raw)["messages"]]


def test_hints_include_concrete_topics_from_the_company_file(env, tmp_path):
    db, settings, servgo, _, _ = env
    folder = tmp_path / "servgo"
    folder.mkdir()
    (folder / "dispatcher_topics.json").write_text(json.dumps([
        {"kind": "post", "topic": "Предоплата напрямую владельцу"},
        {"kind": "video", "topic": "Как работает онлайн-запись для клиента"},
        {"kind": "nonsense"}]), encoding="utf-8")
    state = TestClient(app).get(f"/studio/products/{servgo.id}/dispatch/state").json()
    topics = [h for h in state["hints"] if h["capability"] in {"create_post", "whiteboard_video"}]
    assert [h["label"] for h in topics] == ["Пост: Предоплата напрямую владельцу",
                                            "Видео: Как работает онлайн-запись для клиента"]
    assert topics[0]["params"]["topic"] == "Предоплата напрямую владельцу"


def test_message_flow_over_http(env):
    db, settings, servgo, _, provider = env
    client = TestClient(app)
    body = {"mode": "text", "text": "напиши пост про неявки", "client_key": "t1"}
    response = client.post(f"/studio/products/{servgo.id}/dispatch/messages", json=body)
    assert response.status_code == 200
    data = response.json()
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    assert data["hints"]
    assert db.scalar(select(Task).where(Task.assigned_agent == "content_factory")) is not None
    state = client.get(f"/studio/products/{servgo.id}/dispatch/state").json()
    assert len(state["messages"]) == 2
    again = client.post(f"/studio/products/{servgo.id}/dispatch/messages", json=body).json()
    assert again["replayed"] is True
    assert len(list(db.scalars(select(Task).where(Task.assigned_agent == "content_factory")))) == 1


def test_empty_message_and_unknown_fields_are_rejected(env):
    _, _, servgo, _, provider = env
    client = TestClient(app)
    url = f"/studio/products/{servgo.id}/dispatch/messages"
    assert client.post(url, json={"mode": "text", "text": "   "}).status_code == 422
    assert client.post(url, json={"mode": "text", "text": "hi", "admin": True}).status_code == 422
    assert client.post(url, content=b"not json", headers={"content-type": "application/json"}).status_code == 422
    assert provider.requests == []


def test_new_topic_endpoint_clears_the_thread(env):
    _, _, servgo, _, _ = env
    client = TestClient(app)
    client.post(f"/studio/products/{servgo.id}/dispatch/messages", json=chip_body())
    assert client.post(f"/studio/products/{servgo.id}/dispatch/new").json()["messages"] == []
    assert client.get(f"/studio/products/{servgo.id}/dispatch/state").json()["messages"] == []


def test_unknown_company_is_not_found(env):
    client = TestClient(app)
    assert client.get("/studio/products/00000000-0000-0000-0000-000000000000/dispatch/state").status_code == 404


def test_confirm_endpoint_for_missing_card_is_not_found(env):
    _, _, servgo, _, _ = env
    response = TestClient(app).post(f"/studio/products/{servgo.id}/dispatch/confirm",
                                    json={"confirmation_id": "nope", "accept": True})
    assert response.status_code == 404


def production_settings(tmp_path):
    return Settings(_env_file=None, app_env="production", products_root=tmp_path, studio_admin_username="owner",
                    studio_admin_password="correct-secret", studio_public_origin="https://studio.test")


def test_dispatcher_requires_sign_in_and_same_origin_in_production(env, tmp_path):
    _, _, servgo, _, provider = env
    app.dependency_overrides[get_settings] = lambda: production_settings(tmp_path)
    client = TestClient(app)
    url = f"/studio/products/{servgo.id}/dispatch/messages"
    assert client.post(url, json=chip_body()).status_code == 401
    assert client.get(f"/studio/products/{servgo.id}/dispatch/state").status_code == 401
    auth = ("owner", "correct-secret")
    assert client.post(url, json=chip_body(), auth=auth).status_code == 403  # no Origin header
    cross = client.post(url, json=chip_body(), auth=auth, headers={"origin": "https://evil.example"})
    assert cross.status_code == 403
    ok = client.post(url, json=chip_body(), auth=auth, headers={"origin": "https://studio.test"})
    assert ok.status_code == 200
    assert client.get("/studio/assets/dispatcher.js").status_code == 401


def test_company_account_cannot_use_another_companys_dispatcher(env, tmp_path, monkeypatch):
    db, _, servgo, other, _ = env
    settings = production_settings(tmp_path)
    users = {"salon": {"password_hash": "x", "product_ids": [str(other.id)]}}
    monkeypatch.setattr("app.api.content_studio_auth._load_users", lambda s: users)
    monkeypatch.setattr("app.api.content_studio_auth.check_credentials",
                        lambda s, u, login, password: ("salon", [str(other.id)]) if password == "pw" else None)
    app.dependency_overrides[get_settings] = lambda: settings
    client = TestClient(app)
    auth = ("salon", "pw")
    assert client.get(f"/studio/products/{servgo.id}/dispatch/state", auth=auth).status_code == 404
    assert client.get(f"/studio/products/{other.id}/dispatch/state", auth=auth).status_code == 200
    reply = client.post(f"/studio/products/{other.id}/dispatch/messages", json=chip_body(), auth=auth,
                        headers={"origin": "https://studio.test"})
    assert reply.status_code == 200
    stored = db.scalar(select(DispatcherMessage).where(DispatcherMessage.role == "user"))
    assert stored.product_id == other.id


def test_static_assets_are_served(env):
    client = TestClient(app)
    css = client.get("/studio/assets/dispatcher.css")
    js = client.get("/studio/assets/dispatcher.js")
    assert css.status_code == js.status_code == 200
    assert "text/css" in css.headers["content-type"] and "javascript" in js.headers["content-type"]
    # The script must never put model text into markup.
    assert "innerHTML" not in js.text and "insertAdjacentHTML" not in js.text and "document.write" not in js.text


def test_finished_work_comes_back_over_http_once(env):
    from app.models.domain import TaskStatus
    db, settings, servgo, other, _ = env
    client = TestClient(app)
    client.post(f"/studio/products/{servgo.id}/dispatch/messages", json={
        "mode": "chip", "client_key": "u1", "chip": {"label": "Пост", "capability": "create_post",
                                                    "params": {"topic": "Как снизить неявки"}}})
    assert client.get(f"/studio/products/{servgo.id}/dispatch/updates").json()["messages"] == []
    task = db.scalar(select(Task).where(Task.assigned_agent == "content_factory"))
    task.status = TaskStatus.FAILED
    db.commit()
    first = client.get(f"/studio/products/{servgo.id}/dispatch/updates").json()
    assert len(first["messages"]) == 1 and first["messages"][0]["text"].startswith("Не получилось")
    assert client.get(f"/studio/products/{servgo.id}/dispatch/updates").json()["messages"] == []
    # another company's page never shows it
    assert client.get(f"/studio/products/{other.id}/dispatch/updates").json()["messages"] == []


def test_dispatcher_can_be_enabled_for_every_company_or_a_list():
    from types import SimpleNamespace
    from app.api.dispatcher import enabled_for
    company = SimpleNamespace(slug="company-1")
    assert enabled_for(SimpleNamespace(dispatcher_enabled_slugs="*"), company)
    assert enabled_for(SimpleNamespace(dispatcher_enabled_slugs="servgo, company-1"), company)
    assert not enabled_for(SimpleNamespace(dispatcher_enabled_slugs="servgo"), company)
