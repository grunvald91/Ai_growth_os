import pytest
from fastapi.testclient import TestClient
from passlib.hash import apr_md5_crypt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app import prompts
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture
def prompt_root(tmp_path, monkeypatch):
    root = tmp_path / "products"
    monkeypatch.setattr(prompts, "_root", lambda: root)
    prompts._cache.clear()
    return root


@pytest.fixture
def client(prompt_root):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        def override():
            yield db
        app.dependency_overrides[get_db] = override
        with TestClient(app) as test_client:
            yield test_client, db
        app.dependency_overrides.clear()
    engine.dispose()


def test_only_listed_operator_can_open_tune(tmp_path, prompt_root):
    path = tmp_path / "operators"
    path.write_text("artur:" + apr_md5_crypt.hash("pw-artur") + "\nother:" + apr_md5_crypt.hash("pw-other") + "\n")
    settings = Settings(app_env="production", studio_htpasswd_file=path, studio_tune_usernames="artur",
                        studio_public_origin="https://growth.servgo.ru", _env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as c:
            assert c.get("/studio/tune").status_code == 401
            assert c.get("/studio/tune", auth=("other", "pw-other")).status_code == 403
            page = c.get("/studio/tune", auth=("artur", "pw-artur"))
            assert page.status_code == 200
            assert "Промпты агентов" in page.text
            assert c.get("/studio/tune/work", auth=("other", "pw-other")).status_code == 403
        settings.studio_tune_usernames = ""
        with TestClient(app) as c:
            assert c.get("/studio/tune", auth=("artur", "pw-artur")).status_code == 403  # fail closed
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_prompt_page_lists_every_registered_prompt(client):
    c, _ = client
    page = c.get("/studio/tune")
    assert page.status_code == 200
    for key in ("article.common", "article.rules.marketing_editor", "critic.marketing", "calendar.plan",
                "quick_post.writer", "creative.director", "revision.post"):
        assert f'id="{key}"' in page.text
    assert "Как устроен контент-пакет" in page.text


def test_override_is_used_by_agents_and_can_be_reset(client):
    c, _ = client
    default = prompts.REGISTRY["critic.marketing"].default
    new_text = "Проверь, что текст легко читается и продаёт. " * 3
    r = c.post("/studio/tune/prompts/critic.marketing",
               data={"revision": "0", "action": "save", "text": new_text}, follow_redirects=False)
    assert r.status_code == 303 and "saved=critic.marketing" in r.headers["location"]
    assert prompts.text("critic.marketing") == new_text.strip()
    assert "изменён" in c.get("/studio/tune").text
    r = c.post("/studio/tune/prompts/critic.marketing", data={"revision": "1", "action": "reset", "text": ""},
               follow_redirects=False)
    assert r.status_code == 303
    assert prompts.text("critic.marketing") == default
    assert len(prompts.read_state()["history"]["critic.marketing"]) == 2


def test_placeholders_and_revisions_are_protected(client):
    c, _ = client
    r = c.post("/studio/tune/prompts/calendar.plan",
               data={"revision": "0", "action": "save", "text": "Короткий план без списка источников и подстановок."},
               follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert prompts.text("calendar.plan", evidence_refs="X") == prompts.REGISTRY["calendar.plan"].default.replace("{evidence_refs}", "X")
    c.post("/studio/tune/prompts/revision.post", data={"revision": "0", "action": "save", "text": "Первая правка редактора поста."})
    stale = c.post("/studio/tune/prompts/revision.post",
                   data={"revision": "0", "action": "save", "text": "Вторая правка из старой вкладки."},
                   follow_redirects=False)
    assert "error=" in stale.headers["location"]
    assert prompts.text("revision.post") == "Первая правка редактора поста."


@pytest.mark.asyncio
async def test_work_pages_show_stages_outputs_and_critic(tmp_path, prompt_root):
    from tests.test_articles import setup
    from app.models.domain import Product
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        company = Product(name="ServGo", slug="servgo")
        db.add(company); db.commit()
        workflow, _, request = setup(db, company, tmp_path)
        result = await workflow.run(request)
        assert result.status == "completed", result.error

        def override():
            yield db
        app.dependency_overrides[get_db] = override
        try:
            with TestClient(app) as c:
                listing = c.get("/studio/tune/work")
                assert listing.status_code == 200 and f"/studio/tune/work/{result.task_id}" in listing.text
                detail = c.get(f"/studio/tune/work/{result.task_id}")
                assert detail.status_code == 200
                assert "Маркетинговый редактор" in detail.text
                assert "Результат этапа" in detail.text
                assert "/studio/tune#article.rules.marketing_editor" in detail.text
        finally:
            app.dependency_overrides.clear()
    engine.dispose()
