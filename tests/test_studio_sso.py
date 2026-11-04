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


def make_ticket(*, uid=7, biz=12, role="owner", exp=None, aud="growth-studio", secret=SSO, jti="j" * 16, v=1, name="Салон", origin=None):
    data = {"v": v, "aud": aud, "uid": uid, "biz": biz, "role": role, "name": name, "jti": jti,
            "exp": int(time.time() + 60) if exp is None else exp}
    if origin is not None:
        data["origin"] = origin
    payload = b64(json.dumps(data).encode())
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
        if request.url.path.endswith("/internal/studio/businesses"):
            if self.mode == "legacy":
                return httpx.Response(404)
            if self.mode == "list_down":
                raise httpx.ConnectTimeout("business list unavailable")
            if self.mode == "invalid_list":
                return httpx.Response(200, json=[{"id": "14", "role": "owner"}])
            uid = int(request.url.params["user_id"])
            return httpx.Response(200, json=[
                {"id": biz, "name": self.businesses.get(biz, {}).get("name", f"#{biz}"), "role": role}
                for (user, biz), role in self.allowed.items() if user == uid
            ])
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


def test_sso_owner_can_switch_between_linked_businesses_but_not_other_profiles(env):
    db, _, salon, other, servgo = env
    db.add(StudioBusinessLink(servgo_business_id=14, product_id=other.id))
    unrelated = Product(name="Чужая компания", slug="unrelated")
    db.add(unrelated)
    db.commit()
    servgo.allowed[(7, 14)] = "co_owner"
    client = browser()
    assert client.get("/studio/sso", params={"t": make_ticket()}).status_code == 303
    chooser = client.get("/studio?choose=1")
    assert chooser.status_code == 200
    assert "Салон" in chooser.text and "Другая" in chooser.text
    assert "Чужая компания" not in chooser.text
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{unrelated.id}/dispatch/state").status_code == 404

    # Losing access in ServGo removes a company even if Growth still has its link.
    servgo.allowed.pop((7, 14))
    access.forget()
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 404
    assert "Другая" not in client.get("/studio?choose=1").text


def test_revoking_entry_company_keeps_other_authorized_company(env):
    db, _, salon, other, servgo = env
    db.add(StudioBusinessLink(servgo_business_id=14, product_id=other.id))
    db.commit()
    servgo.allowed[(7, 14)] = "co_owner"
    client = signed_in(env)
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 200
    servgo.allowed.pop((7, 12))
    access.forget()
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 404
    chooser = client.get("/studio?choose=1")
    assert chooser.status_code == 200
    assert "Другая" in chooser.text and "Салон" not in chooser.text
    servgo.allowed.pop((7, 14))
    access.forget()
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 401


def test_empty_business_list_does_not_restore_cached_entry_access(env):
    _, _, salon, _, servgo = env
    client = signed_in(env)  # caches the entry role, but not the business list
    servgo.allowed.clear()
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


@pytest.mark.parametrize("mode", ["legacy", "list_down", "invalid_list"])
def test_failed_list_cannot_keep_other_company_after_entry_access_is_revoked(env, mode):
    db, _, salon, other, servgo = env
    db.add(StudioBusinessLink(servgo_business_id=14, product_id=other.id))
    db.commit()
    servgo.allowed[(7, 14)] = "co_owner"
    client = signed_in(env)
    servgo.allowed.pop((7, 12))
    servgo.mode = mode
    access.forget()
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 401


@pytest.mark.parametrize("mode", ["legacy", "list_down", "invalid_list"])
def test_business_list_failure_falls_back_to_original_ticket_scope(env, mode):
    db, _, salon, other, servgo = env
    db.add(StudioBusinessLink(servgo_business_id=14, product_id=other.id))
    db.commit()
    servgo.allowed[(7, 14)] = "co_owner"
    servgo.mode = mode
    client = browser()
    assert client.get("/studio/sso", params={"t": make_ticket()}).status_code == 303
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 404


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
    # SSO also permits this owner's other existing, linked Studio companies.
    assert client.get(f"/studio/products/{product.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
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
    assert again.status_code == 409 and "Компания уже создана" in again.text and "set-cookie" not in again.headers
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


# ---- canonical ServGo first visit (native React confirmation, not a second login) ----

def canonical_first_visit(env, **ticket):
    from app.core.config import get_settings
    db, settings, _, _, servgo = env
    enabled = make_settings(
        settings.products_root,
        studio_public_origin="https://growth.servgo.ru",
        studio_servgo_gateway_enabled=True,
    )
    app.dependency_overrides[get_settings] = lambda: enabled
    servgo.allowed[(7, 31)] = ticket.pop("role", "owner")
    servgo.businesses[31] = {**servgo.businesses[12], "name": "Баня Парная"}
    client = TestClient(app, base_url="https://servgo.ru", follow_redirects=False)
    client.cookies.set("sg_identity", _browser_identity(7))
    page = client.get("/studio/sso", params={
        "t": make_ticket(biz=31, jti=ticket.pop("jti", "w" * 16), **ticket)
    })
    return client, page


def test_canonical_first_visit_opens_native_servgo_screen_without_creation(env):
    db, *_ = env
    count = len(list(db.scalars(select(Product))))
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    assert page.headers["location"] == "/workspace/growth?setup=1&business_id=31"
    assert page.headers["cache-control"] == "no-store"
    assert page.headers["referrer-policy"] == "no-referrer"
    cookie = page.headers["set-cookie"].lower()
    assert "sg_growth_setup=" in cookie
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert "path=/studio/sso/create" in cookie
    assert SSO not in page.headers["location"]
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None
    assert len(list(db.scalars(select(Product)))) == count


def test_canonical_confirmation_creates_only_selected_company_and_keeps_sso(env):
    db, *_ = env
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    created = client.post("/studio/sso/create", data={"business_id":31},
                          headers={"origin": "https://servgo.ru"})
    assert created.status_code == 303 and created.headers["location"].endswith("/profile")
    link = db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31))
    assert link is not None
    assert "max-age=0" in created.headers.get("set-cookie", "").lower()
    replay = client.post("/studio/sso/create", data={"business_id":31},
                         headers={"origin":"https://servgo.ru"})
    assert replay.status_code == 400
    assert len(list(db.scalars(select(StudioBusinessLink).where(
        StudioBusinessLink.servgo_business_id == 31)))) == 1


def test_canonical_first_visit_refuses_wrong_business_and_foreign_origin(env):
    db, *_ = env
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    wrong = client.post("/studio/sso/create", data={"business_id":12},
                        headers={"origin":"https://servgo.ru"})
    foreign = client.post("/studio/sso/create", data={"business_id":31},
                          headers={"origin":"https://growth.servgo.ru"})
    missing = client.post("/studio/sso/create", data={"business_id":31},
                          headers={"origin":"https://evil.example"})
    assert wrong.status_code == 409
    assert foreign.status_code == 403 and missing.status_code == 403
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_canonical_first_visit_requires_actual_signed_cookie_and_live_owner(env):
    db, _, _, _, servgo = env
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    empty = TestClient(app, base_url="https://servgo.ru", follow_redirects=False)
    without_cookie = empty.post("/studio/sso/create", data={"business_id":31},
                                headers={"origin":"https://servgo.ru"})
    assert without_cookie.status_code == 400
    servgo.allowed.clear()
    revoked = client.post("/studio/sso/create", data={"business_id":31},
                          headers={"origin":"https://servgo.ru"})
    assert revoked.status_code == 403
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_canonical_unlinked_administrator_returns_to_native_servgo(env):
    db, *_ = env
    client, page = canonical_first_visit(env, role="administrator")
    assert page.status_code == 303
    assert page.headers["location"] == "/workspace/growth?setup=owner-required"
    assert "set-cookie" not in page.headers
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_native_setup_recognises_proxy_https_without_allowing_cross_host(env, tmp_path):
    from starlette.requests import Request
    from app.api.studio_sso import _canonical_pwa

    settings = make_settings(tmp_path, studio_servgo_gateway_enabled=True,
                             studio_public_origin="https://growth.servgo.ru")
    def make_request(host, proto, scheme="http"):
        return Request({
            "type":"http", "method":"GET", "scheme":scheme,
            "server":(host,443), "path":"/studio/sso", "query_string":b"",
            "headers":[(b"host",host.encode()), (b"x-forwarded-proto",proto.encode())],
        })
    assert _canonical_pwa(make_request("servgo.ru", "https"), settings)
    assert not _canonical_pwa(make_request("growth.servgo.ru", "https"), settings)
    assert not _canonical_pwa(make_request("servgo.ru", "https,http"), settings)
    assert not _canonical_pwa(make_request("servgo.ru", "http"), settings)

    # nginx->Docker is HTTP but the externally visible host was HTTPS.
    _, _, _, _, servgo = env
    servgo.allowed[(7, 31)] = "owner"
    servgo.businesses[31] = {**servgo.businesses[12], "name":"Баня Парная"}
    from app.core.config import get_settings
    app.dependency_overrides[get_settings] = lambda: settings
    browser = TestClient(app, base_url="http://servgo.ru",
                         headers={"x-forwarded-proto":"https"},
                         follow_redirects=False)
    browser.cookies.set("sg_identity", _browser_identity(7))
    response = browser.get("/studio/sso", params={
        "t": make_ticket(biz=31, jti="n" * 16),
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/workspace/growth?setup=1&business_id=31"
    assert "httponly" in response.headers["set-cookie"].lower()



def test_canonical_browser_errors_return_to_admin_shell_without_cookies(env):
    db, settings, _, _, servgo = env
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    browser_headers = {"accept": "text/html,application/xhtml+xml", "origin": "https://servgo.ru"}
    wrong_business = client.post("/studio/sso/create", data={"business_id": 12},
                                 headers=browser_headers)
    assert wrong_business.status_code == 303
    assert wrong_business.headers["location"] == "/workspace/growth?setup=error&code=business"
    assert "max-age=0" in wrong_business.headers.get("set-cookie", "").lower()
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None

    fresh_client, page = canonical_first_visit(env, jti="y" * 16)
    servgo.allowed.clear()
    blocked = fresh_client.post("/studio/sso/create", data={"business_id":31},
                                headers=browser_headers)
    assert blocked.status_code == 303
    assert blocked.headers["location"] == "/workspace/growth?setup=error&code=denied"
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_canonical_sso_bad_link_browser_returns_to_admin_shell(env):
    client, page = canonical_first_visit(env)
    expired = client.get("/studio/sso?t=bad", headers={"accept":"text/html"})
    assert expired.status_code == 303
    assert expired.headers["location"] == "/workspace/growth?setup=error&code=expired"


def test_legacy_first_visit_form_never_enters_v4_transform(env):
    # The standalone old-domain consent page must remain intact even when
    # a browser has explicitly opted into Growth v4 elsewhere.
    db, settings, _, _, servgo = env
    servgo.allowed[(7, 31)] = "owner"
    servgo.businesses[31] = {**servgo.businesses[12], "name":"Баня Парная"}
    client = browser()
    client.cookies.set("growth_design", "v4")
    page = client.get("/studio/sso", params={"t":make_ticket(biz=31, jti="q"*16)})
    assert page.status_code == 200
    assert 'name="c"' in page.text
    assert 'data-growth-design="v4"' not in page.text


# ---- unified platform identity --------------------------------------------------------------

def test_legacy_operator_is_not_auto_linked_to_servgo_owner(env):
    _, _, salon, _, _ = env
    client = signed_in(env)
    # A business owner has business permissions, not platform operator privileges.
    response = client.get(f"/studio/products/{salon.id}/accounts")
    assert response.status_code == 403
    chooser = client.get("/studio?choose=1")
    assert chooser.status_code == 200
    assert "Объединить профиль с ServGo" in chooser.text


def test_operator_link_needs_both_real_sessions_and_rejects_wrong_password(env):
    _, settings, salon, _, _ = env
    client = signed_in(env)
    assert client.get("/studio/identity").status_code == 200
    denied = client.post("/studio/identity", data={"username": "owner", "password": "wrong-password"},
                         headers={"origin": ORIGIN})
    assert denied.status_code == 401
    from app.core.studio_operator_links import linked_operator
    assert linked_operator(settings, 7, {}) is None
    success = client.post("/studio/identity", data={"username": "owner", "password": "correct-secret"},
                          headers={"origin": ORIGIN})
    assert success.status_code == 303
    assert success.headers["location"] == "/studio/identity?linked=1"
    linked = client.get("/studio/identity")
    assert linked.status_code == 200 and "Профили объединены" in linked.text
    assert "Отвязать профиль Growth" in linked.text
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 200
    # Operator-specific settings gates must recognize "owner" after linking,
    # rather than the raw servgo:<id> identity.
    settings.studio_tune_usernames = "owner"
    assert client.get("/studio/tune").status_code == 200
    from app.core.studio_accounts import accounts_path
    path, _ = accounts_path(settings)
    stored = (path.parent / "servgo_operator_links.json").read_text()
    assert "correct-secret" not in stored and '"login": "owner"' in stored


def test_operator_link_revoked_on_password_change_and_disconnect(env):
    _, settings, salon, _, _ = env
    client = signed_in(env)
    assert client.post("/studio/identity", data={"username": "owner", "password": "correct-secret"},
                       headers={"origin": ORIGIN}).status_code == 303
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 200
    settings.studio_admin_password = "a-different-secret"
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 403
    # Existing owner can explicitly re-bind the new password, then disconnect.
    assert client.post("/studio/identity", data={"username": "owner", "password": "a-different-secret"},
                       headers={"origin": ORIGIN}).status_code == 303
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 200
    assert client.post("/studio/identity/disconnect", headers={"origin": ORIGIN}).status_code == 303
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 403


def test_operator_binding_does_not_bypass_live_servgo_revocation(env):
    _, _, salon, _, servgo = env
    client = signed_in(env)
    assert client.post("/studio/identity", data={"username": "owner", "password": "correct-secret"},
                       headers={"origin": ORIGIN}).status_code == 303
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 200
    servgo.allowed.clear()
    access.forget()
    assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 401


def test_link_attempt_without_servgo_cookie_is_denied(env):
    _, _, _, _, _ = env
    client = browser()
    # A legacy admin login without the separate ServGo session may not bind an ID.
    response = client.post("/studio/identity", data={"username": "owner", "password": "correct-secret"},
                           headers={"origin": ORIGIN}, auth=("owner", "correct-secret"))
    assert response.status_code == 403


def _browser_identity(uid: int, *, nonce: str = "n" * 32, secret: str = SSO) -> str:
    body = b64(json.dumps({"v": 1, "u": uid, "n": nonce,
                           "e": int(time.time()) + 3600}, separators=(",", ":")).encode())
    sig = b64(hmac.new(secret.encode(), b"servgo-browser-identity-v1:" + body.encode(),
                       hashlib.sha256).digest())
    return body + "." + sig


def test_canonical_servgo_sso_overrides_legacy_operator_cookie(env):
    """The canonical origin uses only the currently signed-in ServGo identity."""
    _, settings, salon, other, _ = env
    settings.studio_servgo_gateway_enabled = True
    from app.core.studio_sessions import issue, fingerprint
    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        # Old operator cookies cannot bypass the common account on this host.
        old_owner = issue(settings, "owner", fingerprint("owner\\0correct-secret"))
        client.cookies.set("sg_session", old_owner)
        assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 401

        client.cookies.set("sg_identity", _browser_identity(7))
        response = client.get("/studio/sso", params={"t": make_ticket(jti="canonical-issuer1")})
        assert response.status_code == 303
        cookies = [row.lower() for row in response.headers.get_list("set-cookie")]
        assert any(row.startswith("sg_session=") and "max-age=0" in row for row in cookies)
        assert any(row.startswith("sg_sso=") and "max-age=0" not in row for row in cookies)
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
        assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 404

        # Switching to another ServGo account does not preserve the old Studio login.
        client.cookies.set("sg_identity", _browser_identity(8))
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401

        # Even another login by the SAME user must exchange a fresh one-use ticket.
        client.cookies.set("sg_identity", _browser_identity(7, nonce="z" * 32))
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401
        client.cookies.delete("sg_identity")
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_canonical_sso_ticket_requires_current_servgo_identity(env):
    _, settings, salon, _, _ = env
    settings.studio_servgo_gateway_enabled = True
    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        url = "/studio/sso"
        denied = client.get(url, params={"t": make_ticket(jti="different-user-1")})
        assert denied.status_code == 403
        client.cookies.set("sg_identity", _browser_identity(9))
        denied = client.get(url, params={"t": make_ticket(jti="different-user-2")})
        assert denied.status_code == 403
        client.cookies.set("sg_identity", _browser_identity(7))
        ok = client.get(url, params={"t": make_ticket(jti="different-user-3")})
        assert ok.status_code == 303


def test_canonical_servgo_sso_without_any_legacy_password_configuration(env):
    """Current ServGo login must never depend on old Growth operator passwords."""
    _, settings, salon, _, _ = env
    settings.studio_servgo_gateway_enabled = True
    settings.studio_admin_username = None
    settings.studio_admin_password = None
    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        without_login = client.get(f"/studio/products/{salon.id}/dispatch/state")
        assert without_login.status_code == 401
        client.cookies.set("sg_identity", _browser_identity(7))
        redirect = client.get("/studio/sso", params={"t": make_ticket(jti="no-legacy-accounts1")})
        assert redirect.status_code == 303
        page = client.get(f"/studio/products/{salon.id}/dispatch/state")
        assert page.status_code == 200

        # A different verified account must still be rejected.
        client.cookies.set("sg_identity", _browser_identity(8))
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_legacy_origin_without_password_configuration_stays_closed(env):
    """Removing legacy credentials must not open password-free Growth access."""
    _, settings, salon, _, _ = env
    settings.studio_admin_username = None
    settings.studio_admin_password = None
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as client:
        page = client.get(f"/studio/products/{salon.id}/dispatch/state")
        assert page.status_code == 503


def test_canonical_ticket_cannot_be_redeemed_on_legacy_growth_host(env):
    """A stolen new canonical ticket must not become a Growth cookie via the legacy URL."""
    from app.core.config import get_settings
    _, settings, salon, _, _ = env
    settings.studio_servgo_gateway_enabled = True
    ticket = make_ticket(origin="https://servgo.ru", jti="canonical-only-001")
    with TestClient(app, base_url="https://growth.servgo.ru", follow_redirects=False) as legacy:
        refused = legacy.get("/studio/sso", params={"t": ticket})
        assert refused.status_code == 403
        assert "sg_sso=" not in refused.headers.get("set-cookie", "")
        assert legacy.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401
    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as canonical:
        canonical.cookies.set("sg_identity", _browser_identity(7))
        accepted = canonical.get("/studio/sso", params={"t": ticket})
        assert accepted.status_code == 303
        assert canonical.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200


def test_canonical_ticket_rejects_invalid_signed_destination(env):
    _, settings, _, _, _ = env
    settings.studio_servgo_gateway_enabled = True
    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        client.cookies.set("sg_identity", _browser_identity(7))
        bad = client.get("/studio/sso", params={"t": make_ticket(origin="https://evil.example",
                                                       jti="invalid-origin-01")})
        assert bad.status_code == 400


def test_canonical_setup_intent_is_invalid_after_same_user_login_rotation(env):
    db, _, _, _, _ = env
    client, started = canonical_first_visit(env)
    assert started.status_code == 303
    client.cookies.set("sg_identity", _browser_identity(7, nonce="new-login-" + "x" * 32))
    denied = client.post("/studio/sso/create", data={"business_id": 31},
                         headers={"origin": "https://servgo.ru"})
    assert denied.status_code == 400
    assert db.scalar(select(StudioBusinessLink).where(StudioBusinessLink.servgo_business_id == 31)) is None


def test_canonical_logout_clears_pending_company_setup_cookie(env):
    client, page = canonical_first_visit(env)
    assert page.status_code == 303
    assert "sg_growth_setup" in client.cookies
    logged_out = client.post("/studio/session/logout",
                             headers={"origin": "https://servgo.ru"})
    assert logged_out.status_code == 204
    assert "sg_growth_setup=" in logged_out.headers.get("set-cookie", "") or any(
        h.startswith("sg_growth_setup=") and "max-age=0" in h.lower()
        for h in logged_out.headers.get_list("set-cookie")
    )
    assert "sg_growth_setup" not in client.cookies
    client.cookies.set("sg_identity", _browser_identity(7, nonce="another-login-" + "x" * 32))
    denied = client.post("/studio/sso/create", data={"business_id": 31},
                         headers={"origin": "https://servgo.ru"})
    assert denied.status_code == 400


def test_server_revokes_existing_studio_access_on_next_request_without_cache_flush(env):
    _, _, salon, _, servgo = env
    client = signed_in(env)
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
    servgo.allowed.clear()  # No access.forget(): this models actual owner revocation.
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_fresh_servgo_role_bypasses_stale_positive_cache(env):
    _, settings, _, _, servgo = env
    access.forget()
    assert access.servgo_role(settings, 7, 12, now=1000) == "owner"
    servgo.allowed.clear()
    assert access.servgo_role(settings, 7, 12, now=1001) == "owner"
    assert access.servgo_role(settings, 7, 12, now=1001, fresh=True) is None


def test_canonical_sso_survives_corrupt_legacy_accounts_without_operator_elevation(env, tmp_path):
    """Legacy JSON problems cannot block a tenant or silently grant platform scope."""
    from app.api.content_studio_auth import credential_fingerprint
    from app.core.studio_operator_links import bind_operator, linked_operator
    _, settings, salon, _, _ = env
    corrupt = tmp_path / "corrupt-accounts.json"
    corrupt.write_text("{}", encoding="utf-8")
    settings.studio_users_file = corrupt
    # Establish a real operator link using the SAME account directory, then
    # corrupt that file: this must revoke elevation but not tenant SSO.
    fingerprint = credential_fingerprint(settings, {}, "owner")
    assert fingerprint is not None
    bind_operator(settings, 7, "owner", fingerprint[0])
    assert linked_operator(settings, 7, {}) == "owner"
    corrupt.write_text("{invalid json", encoding="utf-8")
    settings.studio_servgo_gateway_enabled = True

    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        client.cookies.set("sg_identity", _browser_identity(7))
        login = client.get("/studio/sso", params={
            "t": make_ticket(origin="https://servgo.ru", jti="corrupt-accounts-1")
        })
        assert login.status_code == 303
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
        # The link remains present, but legacy account corruption must NOT
        # turn a tenant into a platform administrator.
        assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 403
        assert client.get("/studio/tune").status_code == 403

    # Legacy credential-based sessions still fail closed on a damaged account file.
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as legacy:
        assert legacy.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 503


def test_one_live_business_list_call_authorizes_product_scope_and_role(env):
    """The list already includes trusted current roles; no redundant access request."""
    _, _, salon, _, servgo = env
    client = signed_in(env)
    servgo.calls.clear()
    response = client.get(f"/studio/products/{salon.id}/dispatch/state")
    assert response.status_code == 200
    checks = [request.url.path for request in servgo.calls]
    assert sum(path.endswith("/internal/studio/businesses") for path in checks) == 1
    assert not any(path.endswith("/internal/studio/access") for path in checks)

    # Immediate revocation without waiting for a cache TTL or manual invalidation.
    servgo.allowed.clear()
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 401


def test_one_live_business_list_reflects_role_changes_without_cache(env):
    _, settings, salon, _, servgo = env
    client = signed_in(env)
    assert access.servgo_business_roles(settings, 7) == {12: "owner"}
    servgo.allowed[(7, 12)] = "administrator"
    assert access.servgo_business_roles(settings, 7) == {12: "administrator"}
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200


def test_malformed_live_roles_fall_back_only_to_checked_ticket_company(env):
    db, settings, salon, other, servgo = env
    db.add(StudioBusinessLink(servgo_business_id=14, product_id=other.id))
    db.commit()
    servgo.allowed[(7, 14)] = "co_owner"
    client = signed_in(env)
    servgo.mode = "invalid_list"
    assert access.servgo_business_roles(settings, 7) is None
    assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
    assert client.get(f"/studio/products/{other.id}/dispatch/state").status_code == 404


def test_malformed_legacy_operator_record_cannot_block_or_elevate_canonical_sso(env, tmp_path):
    """Structurally invalid but parseable legacy JSON is not a tenant SSO dependency."""
    from app.api.content_studio_auth import credential_fingerprint
    from app.core.studio_operator_links import bind_operator, linked_operator
    _, settings, salon, _, _ = env
    account_file = tmp_path / "accounts.json"
    account_file.write_text("{}", encoding="utf-8")
    settings.studio_users_file = account_file
    fingerprint = credential_fingerprint(settings, {}, "owner")
    assert fingerprint is not None
    bind_operator(settings, 7, "owner", fingerprint[0])
    assert linked_operator(settings, 7, {}) == "owner"
    # This is a syntactically valid dict, but the owner entry lacks password_hash.
    account_file.write_text('{"owner":{"role":"admin"}}', encoding="utf-8")
    settings.studio_servgo_gateway_enabled = True

    with TestClient(app, base_url="https://servgo.ru", follow_redirects=False) as client:
        client.cookies.set("sg_identity", _browser_identity(7))
        response = client.get("/studio/sso", params={
            "t": make_ticket(origin="https://servgo.ru", jti="malformed-operator-01")
        })
        assert response.status_code == 303
        assert client.get(f"/studio/products/{salon.id}/dispatch/state").status_code == 200
        assert client.get(f"/studio/products/{salon.id}/accounts").status_code == 403

    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as legacy:
        # Old operator credentials must not silently work with malformed entries.
        assert legacy.get(f"/studio/products/{salon.id}/dispatch/state",
                          auth=("owner", "correct-secret")).status_code == 503


def test_malformed_live_role_type_is_rejected_without_server_exception(env):
    _, settings, _, _, servgo = env
    # Validate the malformed payload shape itself; no unhashable role may bubble
    # up into a 500 from the membership check.
    original = servgo.__call__
    class MalformedRoles:
        def __call__(self, request):
            if request.url.path.endswith("/internal/studio/businesses"):
                return httpx.Response(200, json=[{"id": 12, "role": ["owner"]}])
            return original(request)
    access._transport = httpx.MockTransport(MalformedRoles())
    assert access.servgo_business_roles(settings, 7) is None
