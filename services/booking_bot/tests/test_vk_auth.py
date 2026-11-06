"""ServGo-wide VK ID sign-in; never treat provider email/phone as proof of account ownership."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Business, User, VkIdentity, VkLoginTicket, UserAccountMerge, EmailIdentity, YandexIdentity
from database.account_link import merge_yandex_account
from web.api import deps
from web.api.database import get_db
from web.api.routes import vk_auth as vk


class VkAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.previous = (deps.SECRET_KEY, os.environ.get("SERVGO_VK_ID_APP_ID"))
        deps.SECRET_KEY = "v" * 40
        os.environ["SERVGO_VK_ID_APP_ID"] = "12345678"
        self.profile = {"id": "4242", "name": "Иван VK"}
        self.fetches = []

        async def fetch(code, device, state, verifier):
            self.fetches.append((code, device, state, verifier))
            return dict(self.profile)

        self.real_fetch = vk.fetch_vk_profile
        vk.fetch_vk_profile = fetch
        app = FastAPI()
        app.include_router(vk.router, prefix="/api/auth")

        async def database():
            async with self.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    async def asyncTearDown(self):
        self.client.close()
        vk.fetch_vk_profile = self.real_fetch
        deps.SECRET_KEY = self.previous[0]
        if self.previous[1] is None:
            os.environ.pop("SERVGO_VK_ID_APP_ID", None)
        else:
            os.environ["SERVGO_VK_ID_APP_ID"] = self.previous[1]
        await self.engine.dispose()

    async def make_user(self, telegram_id=100, name="Existing"):
        async with self.sessions() as db:
            user = User(telegram_id=telegram_id, full_name=name)
            db.add(user)
            await db.commit()
            return user.id

    def oauth(self, *, auth=None):
        if auth:
            response = self.client.post("/api/auth/vk/link/start", headers=auth)
            self.assertEqual(response.status_code, 200)
            url = response.json()["url"]
        else:
            response = self.client.get("/api/auth/vk/start")
            self.assertEqual(response.status_code, 302)
            url = response.headers["location"]
        state = parse_qs(urlparse(url).query)["state"][0]
        callback = self.client.get("/api/auth/vk/callback", params={
            "state": state, "code": "code", "device_id": "some-device-123"})
        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback.headers["location"].startswith("/login/vk#ticket="))
        return callback.headers["location"].split("#ticket=")[1]

    async def test_pkce_redirect_and_callback(self):
        response = self.client.get("/api/auth/vk/start")
        u = urlparse(response.headers["location"])
        params = parse_qs(u.query)
        self.assertEqual((u.netloc, u.path), ("id.vk.ru", "/authorize"))
        self.assertEqual(params["redirect_uri"], ["https://servgo.ru/api/auth/vk/callback"])
        self.assertEqual(params["scope"], ["email phone"])
        self.assertEqual(params["code_challenge_method"], ["s256"])
        self.assertNotIn("code_verifier", params)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Secure", response.headers["set-cookie"])
        self.assertTrue(self.client.get("/api/auth/vk/capabilities").json()["available"])
        state = params["state"][0]
        callback = self.client.get("/api/auth/vk/callback", params={
            "state": state, "code": "abc", "device_id": "device-12345"})
        self.assertEqual(callback.status_code, 302)
        self.assertEqual(self.fetches[0][2], state)

    async def test_state_mismatch_missing_cookie_and_denied_do_not_exchange(self):
        self.client.get("/api/auth/vk/start")
        self.assertEqual(self.client.get("/api/auth/vk/callback", params={
            "state": "wrong", "code": "abc"}).headers["location"], "/login?vk=expired")
        other = TestClient(self.client.app, base_url="https://testserver", follow_redirects=False)
        self.assertEqual(other.get("/api/auth/vk/callback", params={
            "state": "x", "code": "abc"}).headers["location"], "/login?vk=expired")
        url = self.client.get("/api/auth/vk/start").headers["location"]
        state = parse_qs(urlparse(url).query)["state"][0]
        self.assertEqual(self.client.get("/api/auth/vk/callback", params={
            "state": state, "error": "access_denied"}).headers["location"], "/login?vk=denied")
        self.assertEqual(len(self.fetches), 0)

    async def test_unconfigured_login_is_not_advertised(self):
        os.environ["SERVGO_VK_ID_APP_ID"] = ""
        self.assertFalse(self.client.get("/api/auth/vk/capabilities").json()["available"])
        self.assertEqual(self.client.get("/api/auth/vk/start").headers["location"], "/login?vk=unavailable")

    async def test_new_login_and_returning_login_keep_same_user(self):
        first = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()})
        self.assertEqual(first.status_code, 200)
        data = first.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["role"], "client")
        self.assertTrue(data["token"])
        second = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()})
        self.assertEqual(second.json()["user"]["id"], data["user"]["id"])
        async with self.sessions() as db:
            users = list((await db.execute(select(User))).scalars())
            self.assertEqual(len(users), 1)
            self.assertLess(users[0].telegram_id, 0)

    async def test_one_time_ticket_and_browser_binding(self):
        ticket = self.oauth()
        async with self.sessions() as db:
            stored = (await db.execute(select(VkLoginTicket))).scalars().one()
            self.assertNotEqual(stored.id, ticket)
            self.assertEqual(stored.id, vk._ticket_hash(ticket))
            self.assertNotIn("access_token", stored.payload)
        other = TestClient(self.client.app, base_url="https://testserver", follow_redirects=False)
        self.assertEqual(other.post("/api/auth/vk/exchange", json={"ticket": ticket}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={"ticket": ticket}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={"ticket": ticket}).status_code, 400)

    async def test_no_implicit_merge_by_matching_display_name(self):
        original = await self.make_user(telegram_id=9876, name="Иван VK")
        result = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()}).json()
        self.assertNotEqual(result["user"]["id"], original)
        self.assertEqual(result["role"], "client")

    async def test_link_to_existing_servgo_account_and_studio_role(self):
        target = await self.make_user(telegram_id=777)
        async with self.sessions() as db:
            db.add(Business(owner_id=target, name="Our business", business_type="beauty"))
            await db.commit()
        headers = {"Authorization": "Bearer " + deps.create_token(target)}
        ticket = self.oauth(auth=headers)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={
            "ticket": ticket}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={
            "ticket": ticket}, headers=headers).json(), {"status": "linked"})
        response = self.client.get("/api/auth/vk/account", headers=headers).json()
        self.assertEqual((response["linked"], response["name"]), (True, "Иван VK"))
        login = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()}).json()
        self.assertEqual(login["user"]["id"], target)
        self.assertEqual(login["role"], "admin")

    async def test_link_existing_vk_only_requires_explicit_merge(self):
        former = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()}).json()
        old_id = former["user"]["id"]
        async with self.sessions() as db:
            db.add(Business(owner_id=old_id, name="VK old business", business_type="beauty"))
            await db.commit()
        main = await self.make_user(telegram_id=1234)
        headers = {"Authorization": "Bearer " + deps.create_token(main)}
        ticket = self.oauth(auth=headers)
        pending = self.client.post("/api/auth/vk/exchange", json={"ticket": ticket},
                                   headers=headers)
        self.assertEqual(pending.json(), {"status": "merge_required"})
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(Business.owner_id))).scalar_one(), old_id)
        merged = self.client.post("/api/auth/vk/exchange", json={"ticket": ticket, "merge": True},
                                  headers=headers).json()
        self.assertEqual(merged["user"]["id"], main)
        self.assertEqual(merged["role"], "admin")
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(Business.owner_id))).scalar_one(), main)
            self.assertEqual((await db.get(UserAccountMerge, old_id)).target_user_id, main)
            identity = (await db.execute(select(VkIdentity))).scalar_one()
            self.assertEqual(identity.user_id, main)

    async def test_link_refuses_other_accounts_password_and_wrong_user(self):
        first = await self.make_user(telegram_id=500)
        second = await self.make_user(telegram_id=600)
        ticket = self.oauth(auth={"Authorization": "Bearer " + deps.create_token(first)})
        wrong = self.client.post("/api/auth/vk/exchange", json={"ticket": ticket},
                                 headers={"Authorization": "Bearer " + deps.create_token(second)})
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={"ticket": ticket},
                                          headers={"Authorization": "Bearer " + deps.create_token(first)}).json(),
                         {"status": "linked"})

        # A fully-fledged separately registered VK user must not be seized.
        another = await self.make_user(telegram_id=700)
        headers = {"Authorization": "Bearer " + deps.create_token(another)}
        existing_ticket = self.oauth(auth=headers)
        self.assertEqual(self.client.post("/api/auth/vk/exchange", json={"ticket": existing_ticket},
                                          headers=headers).status_code, 409)


    async def test_merging_legacy_yandex_account_also_preserves_vk_login(self):
        former = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()}).json()
        old_id = former["user"]["id"]
        target_id = await self.make_user(telegram_id=33889)
        async with self.sessions() as db:
            db.add(YandexIdentity(yandex_id="yx-vk-combo", user_id=old_id, login="test"))
            db.add(Business(owner_id=old_id, name="My original business", business_type="beauty"))
            await db.commit()
        async with self.sessions() as db:
            await merge_yandex_account(db, old_id, target_id)
            await db.commit()
            vk_identity = await db.scalar(select(VkIdentity).where(VkIdentity.vk_user_id == "4242"))
            self.assertEqual(vk_identity.user_id, target_id)
            self.assertEqual((await db.execute(select(Business.owner_id))).scalar_one(), target_id)
        # VK login must resolve to the owner, not the old merged account.
        login = self.client.post("/api/auth/vk/exchange", json={"ticket": self.oauth()}).json()
        self.assertEqual((login["user"]["id"], login["role"]), (target_id, "admin"))
