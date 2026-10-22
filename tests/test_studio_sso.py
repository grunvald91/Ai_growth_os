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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
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


def test_unconnected_business_is_told_to_ask_the_owner(env):
    _, _, _, _, servgo = env
    servgo.allowed[(7, 55)] = "owner"
    response = browser().get("/studio/sso", params={"t": make_ticket(biz=55, name="Новая баня", jti="n" * 16)})
    assert response.status_code == 404 and "Новая баня" in response.text and "set-cookie" not in response.headers


def test_unconfigured_studio_does_not_open(env, tmp_path):
    app.dependency_overrides[get_settings] = lambda: make_settings(tmp_path, servgo_internal_key=None)
    response = browser().get("/studio/sso", params={"t": make_ticket()})
    assert response.status_code == 503 and "set-cookie" not in response.headers


def test_user_text_in_the_business_name_is_escaped(env):
    _, _, _, _, servgo = env
    servgo.allowed[(7, 77)] = "owner"
    response = browser().get("/studio/sso", params={"t": make_ticket(biz=77, name="<script>alert(1)</script>", jti="x" * 16)})
    assert "<script>alert" not in response.text and "&lt;script&gt;" in response.text


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


def test_disconnecting_stops_new_sign_ins(env):
    db, settings, salon, *_ = env
    link = db.scalar(select(StudioBusinessLink))
    browser().post(f"/studio/products/{salon.id}/connections/{link.id}/delete", auth=OWNER, headers={"origin": ORIGIN})
    response = browser().get("/studio/sso", params={"t": make_ticket(jti="d" * 16)})
    assert response.status_code == 404
