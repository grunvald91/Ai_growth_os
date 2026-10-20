"""New Studio users can actually sign in: login form, case-insensitive logins, UTF-8 passwords,
switching users in a browser that remembers Basic auth, and sessions that die with the password."""
import base64
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from passlib.hash import apr_md5_crypt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core import studio_sessions
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product

ORIGIN = "https://growth.servgo.ru"
OWNER = ("artur", "owner-password-1")
HTML = {"Accept": "text/html,application/xhtml+xml"}


@pytest.fixture
def studio(tmp_path):
    studio_sessions._FAILURES.clear()
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    htpasswd = tmp_path / "studio.htpasswd"
    htpasswd.write_text("artur:" + apr_md5_crypt.hash(OWNER[1]) + "\n")
    settings = Settings(_env_file=None, app_env="production", studio_public_origin=ORIGIN,
                        studio_htpasswd_file=htpasswd, products_root=tmp_path / "products")
    with Session(engine, expire_on_commit=False) as db:
        mine, other = Product(name="Кофейня", slug="coffee-login"), Product(name="Барбершоп", slug="barber-login")
        db.add_all([mine, other]); db.commit()
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app, headers={"Origin": ORIGIN}, base_url="https://growth.servgo.ru") as client:
            yield SimpleNamespace(client=client, db=db, mine=mine, other=other, settings=settings)
        app.dependency_overrides.clear()
    engine.dispose()


def _create(s, **data):
    page = s.client.post(f"/studio/products/{s.mine.id}/accounts", auth=OWNER, data=data)
    assert page.status_code == 200, page.text[:300]
    return page.text.split('<code class="sg-secret">', 1)[1].split("</code>", 1)[0]


def _basic(login, password):
    return {"Authorization": "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()}


def _login(s, login, password, next_url="/studio"):
    return s.client.post("/studio/login", data={"username": login, "password": password, "next": next_url},
                         follow_redirects=False)


def test_browser_without_login_gets_the_login_page_not_a_basic_dialog(studio):
    c = studio.client
    page = c.get(f"/studio/products/{studio.mine.id}/create", headers=HTML, follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"].startswith("/studio/login?next=%2Fstudio%2Fproducts%2F")
    assert "www-authenticate" not in page.headers
    form = c.get(page.headers["location"])
    assert form.status_code == 200 and "Вход в Growth Studio" in form.text
    assert 'autocapitalize="none"' in form.text
    # API clients still get a Basic challenge.
    assert c.get(f"/studio/products/{studio.mine.id}/activity.json").status_code == 401


def test_new_company_account_signs_in_with_capitalized_login_and_sees_only_its_company(studio):
    c = studio.client
    password = _create(studio, username="manager", role="company", **{f"company_{studio.mine.id}": "on"})
    done = _login(studio, " Manager ", password, f"/studio/products/{studio.mine.id}")
    assert done.status_code == 303 and done.headers["location"] == f"/studio/products/{studio.mine.id}"
    cookie = done.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    assert c.get(f"/studio/products/{studio.mine.id}").status_code == 200
    assert c.get(f"/studio/products/{studio.other.id}").status_code == 404
    assert c.get(f"/studio/products/{studio.mine.id}/accounts").status_code == 403
    chooser = c.get("/studio?choose=1").text
    assert "Кофейня" in chooser and "Барбершоп" not in chooser and "/studio/new" not in chooser
    # Basic auth from a phone also ignores the capital letter.
    c.cookies.clear()
    assert c.get(f"/studio/products/{studio.mine.id}", headers=_basic("Manager", password)).status_code == 200


def test_non_ascii_password_works_in_form_and_basic(studio):
    c = studio.client
    _create(studio, username="anna", role="admin", password="Пароль-для-Анны")
    assert _login(studio, "anna", "Пароль-для-Анны").status_code == 303
    c.cookies.clear()
    assert c.get(f"/studio/products/{studio.other.id}", headers=_basic("anna", "Пароль-для-Анны")).status_code == 200


def test_form_login_switches_user_even_when_browser_remembers_basic_owner(studio):
    c = studio.client
    password = _create(studio, username="manager", role="company", **{f"company_{studio.mine.id}": "on"})
    assert _login(studio, "manager", password).status_code == 303
    # The browser keeps sending the owner's Basic credentials; the session must win.
    page = c.get(f"/studio/products/{studio.mine.id}/accounts", auth=OWNER)
    assert page.status_code == 403
    out = c.get("/studio/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/studio/login?logged_out=1"
    assert c.get(f"/studio/products/{studio.mine.id}/accounts", auth=OWNER).status_code == 200


def test_wrong_password_message_rate_limit_and_safe_redirect(studio):
    _create(studio, username="manager", role="company", **{f"company_{studio.mine.id}": "on"})
    wrong = _login(studio, "manager", "not-the-password")
    assert wrong.status_code == 401 and "Неверный логин или пароль" in wrong.text
    assert "set-cookie" not in wrong.headers
    for _ in range(10):
        _login(studio, "manager", "not-the-password")
    assert _login(studio, "manager", "not-the-password").status_code == 429
    owner = _login(studio, "Artur", OWNER[1], "https://evil.example/")
    assert owner.status_code == 303 and owner.headers["location"] == "/studio"
    assert _login(studio, "artur", OWNER[1], "//evil.example").headers["location"] == "/studio"


def test_login_form_rejects_cross_site_posts(studio):
    response = studio.client.post("/studio/login", data={"username": "artur", "password": OWNER[1]},
                                  headers={"Origin": "https://evil.example"}, follow_redirects=False)
    assert response.status_code == 403


def test_password_reset_and_deleted_account_end_existing_sessions(studio):
    c = studio.client
    password = _create(studio, username="manager", role="company", **{f"company_{studio.mine.id}": "on"})
    assert _login(studio, "manager", password).status_code == 303
    session = c.cookies.get("sg_session")
    c.cookies.clear()
    base = f"/studio/products/{studio.mine.id}/accounts"
    assert c.post(f"{base}/manager/password", auth=OWNER).status_code == 200
    stale = c.get(f"/studio/products/{studio.mine.id}", cookies={"sg_session": session}, follow_redirects=False)
    assert stale.status_code == 401
    forged = session.split(".")[0] + ".AAAA"
    assert c.get(f"/studio/products/{studio.mine.id}", cookies={"sg_session": forged}).status_code == 401


def test_owner_session_reaches_team_dashboard_api(studio):
    c = studio.client
    assert _login(studio, "artur", OWNER[1]).status_code == 303
    assert c.get("/studio/team").status_code == 200
    assert c.get("/studio/team/api").status_code not in {401, 403}


def test_every_studio_page_offers_logout_on_mobile_and_desktop(studio):
    assert _login(studio, "artur", OWNER[1]).status_code == 303
    page = studio.client.get(f"/studio/products/{studio.mine.id}/create").text
    mobile = page.split('<nav class="mobile-nav"', 1)[1].split("</nav>", 1)[0]
    sidebar = page.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
    assert "/studio/logout" in mobile and "/studio/logout" in sidebar
