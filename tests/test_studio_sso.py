"""Sign-in from the ServGo admin: link checking, single use, live access, company scope, owner-only linking."""
import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_db
from app.main import app
from app.models.domain import Product
from app.sso import access, tickets
from app.sso.models import StudioBusinessLink, StudioSsoTicket

SSO = "s" * 40
KEY = "k" * 40
ORIGIN = "https://studio.test"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_ticket(*, uid=7, biz=12, role="owner", exp=None, aud="growth-studio", secret=SSO, jti="j" * 16, v=1, name="Салон"):
    payload = b64(json.dumps({"v": v, "aud": aud, "uid": uid, "biz": biz, "role": role, "name": name, "jti": jti,
                              "exp": int(time.time() + 60) if exp is None else exp}).encode())
    return payload + "." + b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def make_settings(tmp_path, **changes):
    values = dict(_env_file=None, app_env="production", products_root=tmp_path, studio_admin_username="owner",
                  studio_admin_password="correct-secret", studio_public_origin=ORIGIN, studio_session_secret="x" * 40,
                  servgo_sso_secret=SSO, servgo_internal_key=KEY, servgo_api_url="https://servgo.test/api",
                  whiteboard_image_model="google/image")
    values.update(changes)
    return Settings(**values)


class FakeServGo:
    """Stands in for the ServGo server: answers like the real internal endpoint."""

    def __init__(self):
        self.allowed: dict[tuple[int, int], str] = {(7, 12): "owner"}
        self.calls: list[httpx.Request] = []
        self.mode = "normal"
        self.business_mode = "normal"
        self.businesses = {12: {"name": "Салон Лотос", "business_type": "salon", "description": "Маникюр и стрижки",
                                "address": "Сочи, Навагинская 1", "phone": "+7 862 000-00-00",
                                "working_hours": "09:00–21:00", "break_hours": None, "prepayment_percent": 30,
                                "services": [{"name": "Маникюр", "description": None, "tariff": None,
                                              "duration_minutes": 90, "price": 2500}]}}

    def business_answer(self, request: httpx.Request) -> httpx.Response:
        if self.business_mode == "error":
            return httpx.Response(500, json={})
        if request.headers.get("x-studio-key") != KEY:
            return httpx.Response(403, json={})
        pair = (int(request.url.params["user_id"]), int(request.url.params["business_id"]))
        if self.allowed.get(pair) not in ("owner", "co_owner") or pair[1] not in self.businesses:
            return httpx.Response(403, json={})
        return httpx.Response(200, json=self.businesses[pair[1]])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.url.path.endswith("/internal/studio/business"):
            return self.business_answer(request)
        if self.mode == "down":
            raise httpx.ConnectTimeout("timeout")
        if self.mode == "error":
            return httpx.Response(500, json={})
        if request.headers.get("x-studio-key") != KEY:
            return httpx.Response(403, json={})
        pair = (int(request.url.params["user_id"]), int(request.url.params["business_id"]))
        role = self.allowed.get(pair)
        return httpx.Response(200, json={"allowed": role is not None, "role": role})


@pytest.fixture
def env(tmp_path):
    engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    salon, other = Product(name="Салон", slug="salon"), Product(name="Другая", slug="other")
    db.add_all([salon, other])
    db.flush()
    db.add(StudioBusinessLink(servgo_business_id=12, product_id=salon.id))
    db.commit()
    settings = make_settings(tmp_path)

    def override_db():
        yield db
    servgo = FakeServGo()
    access._transport = httpx.MockTransport(servgo)
    access.forget()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    yield db, settings, salon, other, servgo
    access._transport = None
    access.forget()
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def browser():
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


# ---- the link itself -------------------------------------------------------------------------

def test_valid_ticket_is_accepted(env):
    _, settings, *_ = env
    claims = tickets.verify_ticket(settings, make_ticket())
    assert (claims.user_id, claims.business_id, claims.role) == (7, 12, "owner")


@pytest.mark.parametrize("ticket", [
    make_ticket(secret="z" * 40),                 # signed with another secret
    make_ticket(exp=int(time.time()) - 5),        # expired
    make_ticket(exp=int(time.time()) + 3600),     # lives too long to be from ServGo
    make_ticket(aud="somewhere-else"),
    make_ticket(v=2),
    make_ticket(role="root"),
    make_ticket(jti="short"),
    "not-a-ticket", "a.b", "", "." * 5,
])
def test_bad_tickets_are_refused(env, ticket):
    _, settings, *_ = env
    with pytest.raises(tickets.SsoError):
        tickets.verify_ticket(settings, ticket)


def test_tampered_payload_is_refused(env):
    _, settings, *_ = env
    payload, signature = make_ticket(role="administrator").split(".")
    forged = b64(json.dumps({"v": 1, "aud": "growth-studio", "uid": 7, "biz": 99, "role": "owner",
                             "jti": "j" * 16, "exp": int(time.time() + 60)}).encode())
    with pytest.raises(tickets.SsoError):
        tickets.verify_ticket(settings, forged + "." + signature)


def test_short_secrets_switch_the_feature_off(tmp_path):
    for settings in (make_settings(tmp_path, servgo_sso_secret="short"), make_settings(tmp_path, servgo_internal_key=None),
                     make_settings(tmp_path, servgo_internal_key="short"), make_settings(tmp_path, servgo_sso_secret=None)):
        assert not tickets.configured(settings)
    for settings in (make_settings(tmp_path, servgo_sso_secret="short"), make_settings(tmp_path, servgo_sso_secret=None)):
        with pytest.raises(tickets.SsoError):
            tickets.verify_ticket(settings, make_ticket())


# ---- landing page ----------------------------------------------------------------------------

def test_good_link_opens_the_connected_company(env):
    db, settings, salon, other, servgo = env
    client = browser()
    response = client.get("/studio/sso", params={"t": make_ticket()})
    assert response.status_code == 303 and response.headers["location"] == f"/studio/products/{salon.id}"
    assert response.headers["referrer-policy"] == "no-referrer" and response.headers["cache-control"] == "no-store"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    # ServGo was asked with the private key, and the ticket secret never left the studio.
    assert servgo.calls and servgo.calls[0].headers["x-studio-key"] == KEY
    assert SSO not in str(servgo.calls[0].url) and SSO not in str(servgo.calls[0].headers)
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200


def test_the_same_link_works_only_once(env):
    db, settings, salon, *_ = env
    ticket = make_ticket()
    assert browser().get("/studio/sso", params={"t": ticket}).status_code == 303
    second = browser().get("/studio/sso", params={"t": ticket})
    assert second.status_code == 400 and "set-cookie" not in second.headers
    assert db.scalar(select(StudioSsoTicket)).jti == "j" * 16


def test_expired_or_forged_link_shows_a_clear_message(env):
    for ticket in (make_ticket(exp=int(time.time()) - 1), make_ticket(secret="q" * 40), "garbage"):
        response = browser().get("/studio/sso", params={"t": ticket})
        assert response.status_code == 400 and "Ссылка устарела" in response.text and "set-cookie" not in response.headers
    assert browser().get("/studio/sso").status_code == 400


def test_link_is_refused_when_servgo_says_no(env):
    _, _, _, _, servgo = env
    servgo.allowed.clear()
    response = browser().get("/studio/sso", params={"t": make_ticket()})
    assert response.status_code == 403 and "set-cookie" not in response.headers


@pytest.mark.parametrize("mode", ["down", "error"])
def test_link_fails_closed_when_servgo_is_unreachable(env, mode):
    env[4].mode = mode
    response = browser().get("/studio/sso", params={"t": make_ticket()})
    assert response.status_code == 403 and "set-cookie" not in response.headers


def test_unconfigured_studio_does_not_open(env, tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path, servgo_internal_key=None)
    response = browser().get("/studio/sso", params={"t": make_ticket()})
    assert response.status_code == 503 and "set-cookie" not in response.headers


def test_user_text_in_the_business_name_is_escaped(env):
    _, _, _, _, servgo = env
    servgo.allowed[(7, 77)] = "administrator"
    response = browser().get("/studio/sso", params={"t": make_ticket(biz=77, role="administrator",
                                                                    name="<script>alert(1)</script>", jti="x" * 16)})
    assert response.status_code == 403
    assert "<script>alert" not in response.text and "&lt;script&gt;" in response.text


# ---- first visit: the company is created from ServGo data ------------------------------------

def first_visit(env, **ticket):
    _, _, _, _, servgo = env
    servgo.allowed[(7, 31)] = ticket.get("role", "owner")
    servgo.businesses[31] = {**servgo.businesses[12], "name": "Баня Парная"}
    client = browser()
    response = client.get("/studio/sso", params={"t": make_ticket(biz=31, jti=ticket.pop("jti", "f" * 16), **ticket)})
    if response.status_code == 200 and "Создать компанию" in response.text:  # the owner is asked; here they say yes
        response = client.post("/studio/sso/create", data={"c": intent_of(response)})
    return client, response


def intent_of(page):
    import re
    return re.search(r'name="c" value="([^"]+)"', page.text).group(1)


def test_first_visit_creates_the_company_and_starts_collecting_data(env):
    from app.models.domain import Task
    db, settings, salon, other, servgo = env
    client, response = first_visit(env)
    assert response.status_code == 303
    product = db.scalar(select(Product).where(Product.name == "Баня Парная"))
    assert product is not None and product.slug.startswith("company-")
    assert response.headers["location"] == f"/studio/products/{product.id}/profile"
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31))
    assert link.product_id == product.id
    assert db.scalar(select(StudioSsoTicket).where(StudioSsoTicket.servgo_business_id == 31)).product_id == product.id
    assert db.scalar(select(Task).where(Task.product_id == product.id, Task.assigned_agent == "company_intake")) is not None
    page = client.get(response.headers["location"])
    assert page.status_code == 200 and "ServGo: данные заведения" in page.text
    # The new session opens only this company.
    assert client.get(f"/studio/products/{product.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 404
    assert any(call.url.path.endswith("/internal/studio/business") and call.headers["x-studio-key"] == KEY
               for call in servgo.calls)


def test_second_visit_opens_the_same_company_and_does_not_create_another(env):
    db, *_ = env
    first_visit(env)
    count = len(list(db.scalars(select(Product))))
    client = browser()
    response = client.get("/studio/sso", params={"t": make_ticket(biz=31, jti="g" * 16)})
    product = db.scalar(select(Product).where(Product.name == "Баня Парная"))
    assert response.status_code == 303 and response.headers["location"] == f"/studio/products/{product.id}"
    assert len(list(db.scalars(select(Product)))) == count


def test_the_first_visit_link_also_works_only_once(env):
    db, *_ = env
    first_visit(env, jti="h" * 16)
    count = len(list(db.scalars(select(Product))))
    again = browser().get("/studio/sso", params={"t": make_ticket(biz=31, jti="h" * 16)})
    assert again.status_code == 400 and "set-cookie" not in again.headers
    assert len(list(db.scalars(select(Product)))) == count


def test_administrator_cannot_create_the_company(env):
    db, *_ = env
    count = len(list(db.scalars(select(Product))))
    client, response = first_visit(env, role="administrator")
    assert response.status_code == 403 and "Сначала откроет владелец" in response.text
    assert "set-cookie" not in response.headers
    assert len(list(db.scalars(select(Product)))) == count


def test_nothing_is_created_when_servgo_cannot_give_the_data(env):
    db, _, _, _, servgo = env
    servgo.business_mode = "error"
    count = len(list(db.scalars(select(Product))))
    client, response = first_visit(env)
    assert response.status_code == 502 and "set-cookie" not in response.headers
    assert len(list(db.scalars(select(Product)))) == count
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_the_description_comes_only_from_what_servgo_sent(env):
    from app.sso.company import about_text
    db, _, _, _, servgo = env
    first_visit(env)
    product = db.scalar(select(Product).where(Product.name == "Баня Парная"))
    assert "Маникюр — 2 500 ₽, 90 мин" in product.description
    assert "Навагинская" in product.description and "09:00–21:00" in product.description
    assert "предоплатой 30%" in product.description
    assert product.description == about_text(servgo.businesses[31])[:10000]


def test_about_text_handles_empty_and_huge_data():
    from app.sso.company import MAX_ABOUT, about_text
    assert about_text({"name": "Лотос", "services": []}).startswith("Лотос — заведение на платформе")
    huge = about_text({"name": "Х", "services": [{"name": f"Услуга {i}", "price": 100 + i, "duration_minutes": 30}
                                                  for i in range(2000)]})
    assert len(huge) <= MAX_ABOUT and huge.endswith("30 мин")
    odd = about_text({"name": "Х", "services": [{"name": "А", "price": None}, {"price": 5}, "мусор"]})
    assert "- А" in odd and "мусор" not in odd


# ---- the studio session ----------------------------------------------------------------------

def signed_in(env):
    client = browser()
    assert client.get("/studio/sso", params={"t": make_ticket()}).status_code == 303
    return client


def test_access_is_checked_with_servgo_on_every_new_request(env):
    db, settings, salon, other, servgo = env
    client = signed_in(env)
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
    servgo.allowed.clear()      # the owner switched the administrators off / removed the person
    access.forget()             # the few-seconds memory has passed
    refused = client.get(f"/studio/products/{salon.id}/dispatch/state")
    assert refused.status_code == 401
    page = client.get(f"/studio/products/{salon.id}", headers={"accept": "text/html"})
    assert page.status_code == 303 and page.headers["location"].startswith("/studio/login")


def test_access_comes_back_if_servgo_allows_again(env):
    db, settings, salon, other, servgo = env
    client = signed_in(env)
    servgo.allowed.clear(); access.forget()
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401
    servgo.allowed[(7, 12)] = "owner"
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200


def test_servgo_outage_closes_the_studio_for_this_session(env):
    db, settings, salon, other, servgo = env
    client = signed_in(env)
    servgo.mode = "down"; access.forget()
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_positive_answer_is_remembered_only_briefly(env):
    db, settings, salon, other, servgo = env
    access.forget()
    assert access.servgo_role(settings, 7, 12, now=1000.0) == "owner"
    servgo.allowed.clear()
    assert access.servgo_role(settings, 7, 12, now=1000.0 + access.CACHE_SECONDS - 1) == "owner"
    assert access.servgo_role(settings, 7, 12, now=1000.0 + access.CACHE_SECONDS + 1) is None


def test_session_cannot_reach_another_company(env):
    db, settings, salon, other, _ = env
    client = signed_in(env)
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 404
    assert client.get(f"/studio/products/{other.id}").status_code == 404


def test_session_cannot_manage_access_or_delete_the_company(env):
    db, settings, salon, other, _ = env
    client = signed_in(env)
    h = {"origin": ORIGIN}
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 403
    assert client.post(f"/studio/products/{salon.id}/accounts", data={"username": "evil"}, headers=h).status_code == 403
    assert client.post(f"/studio/products/{salon.id}/connections", headers=h,
                       data={"servgo_business_id": "99", "product_id": str(salon.id)}).status_code == 403
    assert client.get(f"/studio/products/{salon.id}/profile/delete").status_code == 403
    assert client.get("/intelligence").status_code == 403
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 99)) is None


def test_session_still_needs_a_same_origin_for_writes(env):
    db, settings, salon, *_ = env
    client = signed_in(env)
    body = {"mode": "chip", "client_key": "k", "chip": {"label": "Что в работе", "capability": "show_work", "params": {}}}
    url = f"/studio/products/{salon.id}/dispatch/messages"
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=body, headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post(url, json=body, headers={"origin": ORIGIN}).status_code == 200


def test_forged_or_foreign_cookies_are_ignored(env, tmp_path):
    db, settings, salon, *_ = env
    claims = tickets.verify_ticket(settings, make_ticket())
    forged = tickets.issue_session(make_settings(tmp_path, studio_session_secret="y" * 40), claims, str(salon.id))
    client = browser()
    client.cookies.set(tickets.COOKIE, forged, domain="studio.test")
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401
    # The ordinary sign-in cookie is not accepted as a ServGo session and the other way round.
    good = tickets.issue_session(settings, claims, str(salon.id))
    from app.core.studio_sessions import COOKIE
    other = browser()
    other.cookies.set(COOKIE, good, domain="studio.test")
    assert other.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_expired_session_cookie_is_ignored(env):
    db, settings, salon, *_ = env
    claims = tickets.verify_ticket(settings, make_ticket())
    old = tickets.issue_session(settings, claims, str(salon.id), now=time.time() - tickets.MAX_AGE - 10)
    client = browser()
    client.cookies.set(tickets.COOKIE, old, domain="studio.test")
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_logout_removes_the_session(env):
    db, settings, salon, *_ = env
    client = signed_in(env)
    out = client.get("/studio/logout")
    assert out.status_code == 303 and tickets.COOKIE in out.headers["set-cookie"]
    client.cookies.clear()
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_conversations_of_a_servgo_person_are_their_own(env):
    from app.dispatcher.models import DispatcherConversation
    db, settings, salon, *_ = env
    client = signed_in(env)
    client.get(f"/studio/products/{salon.id}/dispatch/state")
    assert db.scalar(select(DispatcherConversation)).account == "servgo:7"


# ---- owner connects businesses ---------------------------------------------------------------

OWNER = ("owner", "correct-secret")


def test_owner_connects_changes_and_disconnects_a_business(env):
    db, settings, salon, other, _ = env
    client = browser()
    h = {"origin": ORIGIN}
    page = client.get(f"/studio/products/{salon.id}/accounts", auth=OWNER)
    assert page.status_code == 200 and "Заведения ServGo" in page.text and "№12" in page.text
    ok = client.post(f"/studio/products/{salon.id}/connections", auth=OWNER, headers=h,
                     data={"servgo_business_id": "31", "product_id": str(other.id)})
    assert ok.status_code == 303
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31))
    assert link.product_id == other.id
    again = client.post(f"/studio/products/{salon.id}/connections", auth=OWNER, headers=h,
                        data={"servgo_business_id": "31", "product_id": str(salon.id)})
    assert again.status_code == 303 and db.scalar(select(StudioBusinessLink).where(
        StudioBusinessLink.servgo_business_id == 31)).product_id == salon.id
    gone = client.post(f"/studio/products/{salon.id}/connections/{link.id}/delete", auth=OWNER, headers=h)
    assert gone.status_code == 303
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


@pytest.mark.parametrize("business_id,product", [("abc", "ok"), ("", "ok"), ("-3", "ok"), ("0", "ok"),
                                                 ("99999999999", "ok"), ("5", "not-a-uuid"),
                                                 ("5", "00000000-0000-0000-0000-000000000000")])
def test_bad_connection_input_is_rejected(env, business_id, product):
    db, settings, salon, *_ = env
    product_id = str(salon.id) if product == "ok" else product
    response = browser().post(f"/studio/products/{salon.id}/connections", auth=OWNER, headers={"origin": ORIGIN},
                              data={"servgo_business_id": business_id, "product_id": product_id})
    assert response.status_code == 422
    assert len(list(db.scalars(select(StudioBusinessLink)))) == 1


def test_after_disconnecting_the_next_visit_starts_a_new_company(env):
    db, settings, salon, *_ = env
    link = db.scalar(select(StudioBusinessLink))
    browser().post(f"/studio/products/{salon.id}/connections/{link.id}/delete", auth=OWNER, headers={"origin": ORIGIN})
    client = browser()
    page = client.get("/studio/sso", params={"t": make_ticket(jti="d" * 16)})
    assert page.status_code == 200 and "Создать компанию" in page.text  # the owner is asked again
    response = client.post("/studio/sso/create", data={"c": intent_of(page)})
    assert response.status_code == 303 and response.headers["location"].endswith("/profile")
    assert db.scalar(select(Product).where(Product.name == "Салон Лотос")) is not None


def test_disconnecting_ends_existing_sessions_at_once(env):
    db, settings, salon, *_ = env
    client = signed_in(env)
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
    link = db.scalar(select(StudioBusinessLink))
    browser().post(f"/studio/products/{salon.id}/connections/{link.id}/delete", auth=OWNER, headers={"origin": ORIGIN})
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_relinking_to_another_company_ends_the_old_session(env):
    db, settings, salon, other, _ = env
    client = signed_in(env)
    browser().post(f"/studio/products/{salon.id}/connections", auth=OWNER, headers={"origin": ORIGIN},
                   data={"servgo_business_id": "12", "product_id": str(other.id)})
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 401


def test_a_link_from_someone_else_cannot_push_the_owner_out(env):
    """The studio owner signed in with a password; a ServGo link for another business must not replace that."""
    db, settings, salon, other, servgo = env
    client = browser()
    assert client.post("/studio/login", data={"username": "owner", "password": "correct-secret", "next": "/studio"},
                       headers={"origin": ORIGIN}).status_code == 303
    response = client.get("/studio/sso", params={"t": make_ticket(jti="o" * 16)})
    assert response.status_code == 303
    assert not any(c.lower().startswith("sg_session=") for c in response.headers.get_list("set-cookie"))
    # Still the owner: sees every company and the accounts page.
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 200


def test_a_company_password_session_for_other_companies_is_replaced(env, monkeypatch):
    db, settings, salon, other, servgo = env
    from app.core import studio_sessions
    from app.api import content_studio_auth
    monkeypatch.setattr(content_studio_auth, "_load_users", lambda s: {"x": {"password_hash": "h", "product_ids": [str(other.id)]}})
    monkeypatch.setattr(content_studio_auth, "credential_fingerprint", lambda s, u, login: ("f", [str(other.id)]))
    client = browser()
    client.cookies.set(studio_sessions.COOKIE, studio_sessions.issue(settings, "x", "f"), domain="studio.test")
    response = client.get("/studio/sso", params={"t": make_ticket(jti="p" * 16)})
    assert response.status_code == 303
    cookies = [c.lower() for c in response.headers.get_list("set-cookie")]
    assert any(c.startswith("sg_session=") and "max-age=0" in c for c in cookies)   # dropped
    assert any(c.startswith("sg_sso=") and "max-age=0" not in c for c in cookies)    # ServGo session set


def test_about_text_describes_rental_halls_and_per_person_prices():
    from app.sso.company import about_text
    text = about_text({"name": "Лофт", "services": [{"name": "Аренда", "price": 1500, "per_person": True}],
                       "resources": [{"name": "Зал Лофт", "capacity": 20, "area_sqm": 80, "description": "Окна в пол"}]})
    assert "Аренда — 1 500 ₽ с человека" in text
    assert "- Зал Лофт — до 20 человек, 80 м². Окна в пол" in text


# ---- the owner is asked before the company is created ----------------------------------------------

def asked(env, **ticket):
    _, _, _, _, servgo = env
    servgo.allowed[(7, 31)] = ticket.pop("role", "owner")
    servgo.businesses[31] = {**servgo.businesses[12], "name": "Баня Парная"}
    client = browser()
    return client, client.get("/studio/sso", params={"t": make_ticket(biz=31, jti=ticket.pop("jti", "q" * 16), **ticket)})


def test_the_owner_is_asked_and_nothing_is_created_until_they_say_yes(env):
    db, *_ = env
    count = len(list(db.scalars(select(Product))))
    _, page = asked(env)
    assert page.status_code == 200 and "Создать компанию" in page.text and "Не сейчас" in page.text
    assert "set-cookie" not in page.headers and page.headers["cache-control"] == "no-store"
    assert len(list(db.scalars(select(Product)))) == count
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_saying_yes_creates_the_company_once(env):
    db, *_ = env
    client, page = asked(env)
    token = intent_of(page)
    done = client.post("/studio/sso/create", data={"c": token})
    assert done.status_code == 303 and done.headers["location"].endswith("/profile")
    count = len(list(db.scalars(select(Product))))
    again = browser().post("/studio/sso/create", data={"c": token})  # pressing twice or replaying the form
    assert len(list(db.scalars(select(Product)))) == count
    assert again.status_code in (303, 400)
    assert len(list(db.scalars(select(Product).where(Product.name == "Баня Парная")))) == 1


def test_the_question_cannot_be_forged_or_stale(env):
    from app.core.config import get_settings
    db, settings, *_ = env
    _, page = asked(env)
    token = intent_of(page)
    forged = browser().post("/studio/sso/create", data={"c": token[:-3] + "AAA"})
    empty = browser().post("/studio/sso/create", data={"c": ""})
    assert forged.status_code == 400 and empty.status_code == 400
    stale = tickets.issue_intent(settings, tickets.read_intent(settings, token), now=time.time() - 3600)
    assert browser().post("/studio/sso/create", data={"c": stale}).status_code == 400
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_yes_is_refused_if_servgo_no_longer_confirms_the_owner(env):
    db, _, _, _, servgo = env
    client, page = asked(env)
    servgo.allowed.clear()
    answer = client.post("/studio/sso/create", data={"c": intent_of(page)})
    assert answer.status_code == 403 and "set-cookie" not in answer.headers
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_the_business_name_in_the_question_is_escaped(env):
    _, page = asked(env, name="<script>alert(1)</script>")
    assert "<script>alert" not in page.text and "&lt;script&gt;" in page.text
