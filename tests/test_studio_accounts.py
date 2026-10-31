"""Accounts created in Growth Studio; the app alone guards every private route."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from passlib.hash import apr_md5_crypt
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Artifact, ArtifactStatus, Product, Task, TaskStatus

ORIGIN = "https://growth.servgo.ru"
OWNER = ("owner", "owner-password-1")


@pytest.fixture
def studio(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    htpasswd = tmp_path / "studio.htpasswd"
    htpasswd.write_text("owner:" + apr_md5_crypt.hash(OWNER[1]) + "\n")
    settings = Settings(_env_file=None, app_env="production", studio_public_origin=ORIGIN,
                        studio_htpasswd_file=htpasswd, products_root=tmp_path / "products")
    with Session(engine, expire_on_commit=False) as db:
        mine, other = Product(name="Кофейня", slug="coffee-acc"), Product(name="Барбершоп", slug="barber-acc")
        db.add_all([mine, other]); db.commit()

        def session_override():
            yield db
        app.dependency_overrides[get_db] = session_override
        app.dependency_overrides[get_settings] = lambda: settings
        with TestClient(app, headers={"Origin": ORIGIN}) as client:
            yield SimpleNamespace(client=client, db=db, mine=mine, other=other, settings=settings)
        app.dependency_overrides.clear()
    engine.dispose()


def _password(page_text):
    marker = '<code class="sg-secret">'
    return page_text.split(marker, 1)[1].split("</code>", 1)[0]


def test_owner_creates_company_and_admin_accounts(studio):
    c, mine, other = studio.client, studio.mine, studio.other
    url = f"/studio/products/{mine.id}/accounts"
    assert c.get(url).status_code == 401
    page = c.get(url, auth=OWNER)
    assert page.status_code == 200 and "Новый аккаунт" in page.text and "owner" in page.text

    created = c.post(url, auth=OWNER, data={"username": "Coffee.Team", "role": "company", f"company_{mine.id}": "on"})
    assert created.status_code == 200 and "Аккаунт создан" in created.text
    team = ("coffee.team", _password(created.text))
    stored = json.loads((studio.settings.products_root / "_studio" / "accounts.json").read_text())
    assert team[1] not in json.dumps(stored) and stored["coffee.team"]["product_ids"] == [str(mine.id)]

    assert c.get(f"/studio/products/{mine.id}/create", auth=team).status_code == 200
    assert c.get(f"/studio/products/{other.id}/create", auth=team).status_code == 404
    assert c.get(url, auth=team).status_code == 403
    assert c.get("/intelligence", auth=team).status_code == 403
    assert c.get(f"/studio/products/{mine.id}/create", auth=("coffee.team", "wrong-password")).status_code == 401

    # Operator logins are reserved; duplicate and invalid requests are explained.
    assert c.post(url, auth=OWNER, data={"username": "owner", "role": "admin", "password": "x" * 12}).status_code == 422
    assert c.post(url, auth=OWNER, data={"username": "coffee.team", "role": "admin"}).status_code == 422
    assert c.post(url, auth=OWNER, data={"username": "nobody", "role": "company"}).status_code == 422

    admin = c.post(url, auth=OWNER, data={"username": "deputy", "role": "admin", "password": "deputy-password"})
    assert admin.status_code == 200
    deputy = ("deputy", "deputy-password")
    assert c.get(f"/studio/products/{other.id}/create", auth=deputy).status_code == 200
    assert c.get(url, auth=deputy).status_code == 200

    reset = c.post(f"{url}/coffee.team/password", auth=deputy)
    assert reset.status_code == 200 and "Пароль изменён" in reset.text
    assert c.get(f"/studio/products/{mine.id}/create", auth=team).status_code == 401
    assert c.get(f"/studio/products/{mine.id}/create", auth=("coffee.team", _password(reset.text))).status_code == 200

    assert c.post(f"{url}/deputy/delete", auth=deputy).status_code == 409  # not yourself
    assert c.post(f"{url}/coffee.team/delete", auth=deputy).status_code == 200
    assert c.get(f"/studio/products/{mine.id}/create", auth=team).status_code == 401
    assert c.post(url, auth=OWNER, data={"username": "evil", "role": "admin"},
                  headers={"Origin": "https://evil.example"}).status_code == 403


# The sign-in page must be reachable without a login; logout only clears the cookie.
PUBLIC = {"/health", "/health/version", "/studio/login", "/studio/logout", "/studio/sso", "/studio/sso/create",  # sso: signed one-time link from ServGo
          # Read-only feed of already published blog articles; the public blog on servgo.ru renders pages from it.
          "/public/blog/{product_slug}/articles", "/public/blog/{product_slug}/articles/{slug}",
          "/public/blog/businesses/with-articles",
          "/public/blog/businesses/{business_id}/articles",
          "/public/blog/businesses/{business_id}/articles/{slug}"}


def test_every_private_route_requires_studio_login(studio):
    # Nginx no longer checks passwords: the application must guard every route.
    import re
    open_routes = []
    for path, methods in app.openapi()["paths"].items():
        if path in PUBLIC:
            continue
        concrete = re.sub(r"\{[^}]*id\}", "00000000-0000-0000-0000-000000000001", path)
        concrete = re.sub(r"\{[^}]*\}", "x", concrete)
        for method in methods:
            response = studio.client.request(method.upper(), concrete)
            # The team API also accepts a bearer token; without one it fails closed (503).
            guarded = response.status_code == 401 or (path.startswith("/api/admin/") and response.status_code == 503)
            if not guarded:
                open_routes.append((method, path, response.status_code))
    assert len(app.openapi()["paths"]) > 100
    assert open_routes == [], "\n".join(map(str, open_routes))


def test_read_all_hides_current_notifications_only(studio):
    c, db, mine = studio.client, studio.db, studio.mine
    task = Task(product_id=mine.id, title="t", assigned_agent="content_factory", status=TaskStatus.COMPLETED)
    db.add(task); db.flush()
    first = Artifact(product_id=mine.id, task_id=task.id, title="Пост", kind="channel_text",
                     status=ArtifactStatus.AWAITING_APPROVAL, content="{}")
    db.add(first); db.commit()
    feed = f"/studio/products/{mine.id}/activity.json"
    notes = c.get(feed, auth=OWNER).json()["notices"]
    assert [n["id"] for n in notes] == [str(first.id)]
    page = c.get(f"/studio/products/{mine.id}", auth=OWNER).text
    assert "Прочитать все" in page
    done = c.post(f"/studio/products/{mine.id}/notifications/read-all", auth=OWNER, json={"ids": [n["id"] for n in notes]})
    assert done.status_code == 200 and done.json() == {"marked": 1}
    assert c.get(feed, auth=OWNER).json()["notices"] == []
    second = Artifact(product_id=mine.id, task_id=task.id, title="Новый пост", kind="channel_text",
                      status=ArtifactStatus.AWAITING_APPROVAL, content="{}")
    db.add(second); db.commit()
    assert [n["id"] for n in c.get(feed, auth=OWNER).json()["notices"]] == [str(second.id)]
    assert db.get(Artifact, first.id).status == ArtifactStatus.AWAITING_APPROVAL


def test_every_desktop_section_is_reachable_on_mobile(studio):
    # Mobile is the primary view: every sidebar link must also be in the bottom nav or "Ещё".
    import re
    page = studio.client.get(f"/studio/products/{studio.mine.id}/create", auth=OWNER).text
    sidebar = page.split('<aside class="sidebar">', 1)[1].split("</aside>", 1)[0]
    mobile = page.split('<nav class="mobile-nav"', 1)[1].split("</nav>", 1)[0]
    desktop_links = set(re.findall(r'href="([^"]+)"', sidebar))
    mobile_links = set(re.findall(r'href="([^"]+)"', mobile))
    assert desktop_links - mobile_links == set()
    assert "/accounts" in " ".join(mobile_links) and "Ещё" in mobile
