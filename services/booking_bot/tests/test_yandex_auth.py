"""Sign in with Yandex ID: state checks, one-time tickets, phone join only after a Telegram code."""
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

from database.models import Base, Business, EmailIdentity, User, UserAccountMerge, YandexIdentity, YandexTicket
from web.api import deps
from web.api.database import get_db
from web.api.routes import yandex_auth as yx


def info(**changes):
    value = {"id": "1000", "login": "ivan", "real_name": "Иван Петров", "default_email": "Ivan@Yandex.ru",
             "default_phone": {"id": 1, "number": "+79180001122"}, "birthday": "1990-05-17"}
    value.update(changes)
    return yx.profile_from(value)


class YandexAuthTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        os.environ.update(YANDEX_CLIENT_ID="cid", YANDEX_CLIENT_SECRET="csecret")
        self._secret = deps.SECRET_KEY
        deps.SECRET_KEY = "x" * 40
        self.profile = info()
        self.fetched = []
        self.sent = []
        self.send_ok = True

        async def fake_fetch(code, verifier):
            self.fetched.append((code, verifier))
            return self.profile

        async def fake_send(target, code):
            self.sent.append((target.id, code))
            return self.send_ok

        self._originals = (yx.fetch_profile, yx._send_code)
        yx.fetch_profile, yx._send_code = fake_fetch, fake_send
        app = FastAPI()
        app.include_router(yx.router, prefix="/api/auth")

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    async def asyncTearDown(self):
        yx.fetch_profile, yx._send_code = self._originals
        deps.SECRET_KEY = self._secret
        self.client.close()
        await self.engine.dispose()

    # ---- helpers
    def begin(self, headers=None, link=False):
        """Run start -> callback and return the one-time ticket from the redirect fragment."""
        if link:
            url = self.client.post("/api/auth/yandex/link/start", headers=headers).json()["url"]
        else:
            start = self.client.get("/api/auth/yandex/start")
            self.assertEqual(start.status_code, 302)
            url = start.headers["location"]
        state = parse_qs(urlparse(url).query)["state"][0]
        back = self.client.get("/api/auth/yandex/callback", params={"code": "abc", "state": state})
        self.assertEqual(back.status_code, 302)
        location = back.headers["location"]
        self.assertTrue(location.startswith("/login/yandex#ticket="), location)
        return location.split("#ticket=")[1]

    async def add_user(self, **fields):
        async with self.sessions() as db:
            user = User(**fields)
            db.add(user)
            await db.commit()
            return user.id

    async def users(self):
        async with self.sessions() as db:
            return list((await db.execute(select(User))).scalars())

    async def identities(self):
        async with self.sessions() as db:
            return list((await db.execute(select(YandexIdentity))).scalars())

    # ---- start / callback
    async def test_start_sends_the_person_to_yandex_with_pkce_and_state(self):
        response = self.client.get("/api/auth/yandex/start")
        url = urlparse(response.headers["location"])
        query = parse_qs(url.query)
        self.assertEqual((url.netloc, url.path), ("oauth.yandex.ru", "/authorize"))
        self.assertEqual(query["client_id"], ["cid"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["redirect_uri"], ["https://servgo.ru/api/auth/yandex/callback"])
        self.assertIn("servgo_yx", response.headers["set-cookie"])
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    async def test_start_without_configuration_goes_back_to_login(self):
        os.environ["YANDEX_CLIENT_SECRET"] = ""
        self.assertEqual(self.client.get("/api/auth/yandex/start").headers["location"], "/login?yandex=unavailable")
        self.assertFalse(self.client.get("/api/auth/yandex/capabilities").json()["available"])

    async def test_callback_with_wrong_or_missing_state_is_refused_and_asks_yandex_for_nothing(self):
        self.client.get("/api/auth/yandex/start")
        bad = self.client.get("/api/auth/yandex/callback", params={"code": "abc", "state": "forged"})
        self.assertEqual(bad.headers["location"], "/login?yandex=expired")
        fresh = TestClient(self.client.app, base_url="https://testserver", follow_redirects=False)
        none = fresh.get("/api/auth/yandex/callback", params={"code": "abc", "state": "x"})
        self.assertEqual(none.headers["location"], "/login?yandex=expired")
        self.assertEqual(self.fetched, [])

    async def test_cookie_signed_with_another_key_is_refused(self):
        self.client.get("/api/auth/yandex/start")
        deps.SECRET_KEY = "y" * 40
        back = self.client.get("/api/auth/yandex/callback", params={"code": "abc", "state": "whatever"})
        self.assertEqual(back.headers["location"], "/login?yandex=expired")

    async def test_denied_by_the_person(self):
        url = self.client.get("/api/auth/yandex/start").headers["location"]
        state = parse_qs(urlparse(url).query)["state"][0]
        back = self.client.get("/api/auth/yandex/callback", params={"error": "access_denied", "state": state})
        self.assertEqual(back.headers["location"], "/login?yandex=denied")

    async def test_ticket_is_stored_only_as_a_hash(self):
        ticket = self.begin()
        async with self.sessions() as db:
            row = (await db.execute(select(YandexTicket))).scalars().one()
        self.assertNotIn(ticket, row.id)
        self.assertEqual(row.id, yx._ticket_hash(ticket))

    # ---- new person
    async def test_new_person_gets_an_account_with_phone_email_and_birthday(self):
        ticket = self.begin()
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).json()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["role"], "client")
        user = (await self.users())[0]
        self.assertLess(user.telegram_id, 0)
        self.assertEqual((user.full_name, user.email, user.phone, user.birthday),
                         ("Иван Петров", "ivan@yandex.ru", "+79180001122", "1990-05-17"))
        self.assertEqual([(i.yandex_id, i.user_id) for i in await self.identities()], [("1000", user.id)])
        self.assertEqual(deps.jwt.decode(result["token"], deps.SECRET_KEY, algorithms=["HS256"])["sub"], str(user.id))

    async def test_ticket_works_once(self):
        ticket = self.begin()
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).status_code, 200)
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": "z" * 43}).status_code, 400)

    async def test_returning_person_signs_in_to_the_same_account(self):
        first = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        second = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertEqual(first["user"]["id"], second["user"]["id"])
        self.assertEqual(len(await self.users()), 1)

    async def test_birthday_with_unknown_year_and_bad_values(self):
        self.assertEqual(info(birthday="0000-05-17")["birthday"], "0000-05-17")
        for bad in ("0000-00-17", "1990-13-01", "1990-05-00", "17.05.1990", None, 5):
            self.assertIsNone(info(birthday=bad)["birthday"], bad)
        self.assertIsNone(info(default_phone={"number": "abc"})["phone"])
        self.assertEqual(info(real_name="", first_name="Анна", last_name="К")["name"], "Анна К")

    # ---- phone join with a code from Telegram
    async def test_same_phone_as_a_telegram_account_needs_a_code_from_that_telegram(self):
        telegram = await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        ticket = self.begin()
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).json(), {"status": "code_required", "account": "new"})
        self.assertEqual(len(await self.users()), 1)
        self.assertEqual(await self.identities(), [])
        (target, code), = self.sent
        self.assertEqual(target, telegram)
        wrong = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": "000000" if code != "000000" else "111111"})
        self.assertEqual(wrong.status_code, 400)
        right = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": code}).json()
        self.assertEqual((right["status"], right["user"]["id"]), ("ok", telegram))
        self.assertEqual(len(await self.users()), 1)
        self.assertEqual([(i.yandex_id, i.user_id) for i in await self.identities()], [("1000", telegram)])
        user = (await self.users())[0]
        self.assertEqual((user.email, user.birthday), ("ivan@yandex.ru", "1990-05-17"))

    async def test_code_is_not_resent_inside_a_minute(self):
        await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        ticket = self.begin()
        for _ in range(3):
            self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket})
        self.assertEqual(len(self.sent), 1)

    async def test_five_wrong_codes_burn_the_ticket(self):
        await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        ticket = self.begin()
        self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket})
        code = self.sent[0][1]
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": wrong}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": code}).status_code, 400)
        self.assertEqual(await self.identities(), [])
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": code}).status_code, 400)

    async def test_code_from_another_ticket_does_not_work(self):
        await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        first = self.begin()
        self.client.post("/api/auth/yandex/exchange", json={"ticket": first})
        code = self.sent[0][1]
        second = self.begin()
        self.client.post("/api/auth/yandex/exchange", json={"ticket": second})
        stolen = self.client.post("/api/auth/yandex/exchange", json={"ticket": second, "code": code})
        self.assertEqual(stolen.status_code, 400)

    async def test_ticket_opened_in_another_browser_is_refused(self):
        ticket = self.begin()
        other = TestClient(self.client.app, base_url="https://testserver", follow_redirects=False)
        self.assertEqual(other.post("/api/auth/yandex/exchange", json={"ticket": ticket}).status_code, 400)
        self.assertEqual(await self.users(), [])
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).status_code, 200)

    async def test_when_telegram_cannot_be_reached_the_person_may_continue_separately(self):
        await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        self.send_ok = False
        ticket = self.begin()
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).json(), {"status": "code_unavailable", "account": "new"})
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "separate": True}).json()
        self.assertEqual(result["status"], "ok")
        users = await self.users()
        self.assertEqual(len(users), 2)
        new = next(u for u in users if u.id == result["user"]["id"])
        self.assertIsNone(new.phone)  # the number stays with the Telegram account

    async def test_telegram_account_that_already_has_another_yandex_is_not_offered(self):
        telegram = await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        async with self.sessions() as db:
            db.add(YandexIdentity(yandex_id="other", user_id=telegram, login="x"))
            await db.commit()
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertEqual(result["status"], "ok")
        self.assertNotEqual(result["user"]["id"], telegram)
        self.assertEqual(self.sent, [])

    async def test_email_account_with_same_phone_is_never_joined_automatically(self):
        email_user = await self.add_user(telegram_id=-5, full_name="Почта", phone="+79180001122")
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertEqual(result["status"], "ok")
        self.assertNotEqual(result["user"]["id"], email_user)
        self.assertEqual(self.sent, [])

    # ---- link from the profile
    async def test_signed_in_person_can_attach_yandex(self):
        mine = await self.add_user(telegram_id=555, full_name="Я")
        headers = {"Authorization": "Bearer " + deps.create_token(mine)}
        ticket = self.begin(headers, link=True)
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}, headers=headers).json(), {"status": "linked"})
        self.assertEqual([(i.yandex_id, i.user_id) for i in await self.identities()], [("1000", mine)])
        account = self.client.get("/api/auth/yandex/account", headers=headers).json()
        self.assertEqual((account["linked"], account["login"]), (True, "ivan"))
        # and now this Yandex signs in to the same account
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"], mine)

    async def test_someone_else_cannot_use_my_link_ticket(self):
        mine = await self.add_user(telegram_id=555, full_name="Я")
        other = await self.add_user(telegram_id=556, full_name="Чужой")
        ticket = self.begin({"Authorization": "Bearer " + deps.create_token(mine)}, link=True)
        response = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket},
                                    headers={"Authorization": "Bearer " + deps.create_token(other)})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(await self.identities(), [])

    async def test_yandex_taken_by_another_account_cannot_be_attached(self):
        first = await self.add_user(telegram_id=555, full_name="Первый")
        second = await self.add_user(telegram_id=556, full_name="Второй")
        async with self.sessions() as db:
            db.add(YandexIdentity(yandex_id="1000", user_id=first, login="ivan"))
            await db.commit()
        headers = {"Authorization": "Bearer " + deps.create_token(second)}
        response = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin(headers, link=True)}, headers=headers)
        self.assertEqual(response.status_code, 409)

    async def test_linking_yandex_from_telegram_joins_the_earlier_yandex_only_account(self):
        created = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        yandex_only = created["user"]["id"]
        self.assertTrue(self.client.get("/api/auth/yandex/account", headers={
            "Authorization": "Bearer " + created["token"]}).json()["telegram_join"])
        async with self.sessions() as db:
            db.add(Business(owner_id=yandex_only, name="Мой бизнес", business_type="beauty"))
            await db.commit()
        telegram = await self.add_user(telegram_id=555, full_name="Я в Telegram", phone="79180001122")  # bot style
        headers = {"Authorization": "Bearer " + deps.create_token(telegram)}
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin(headers, link=True)}, headers=headers).json()
        self.assertEqual((result["status"], result["user"]["id"], result["role"]), ("ok", telegram, "admin"))
        self.assertEqual([(i.yandex_id, i.user_id) for i in await self.identities()], [("1000", telegram)])
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(Business.owner_id))).scalar_one(), telegram)
            self.assertEqual((await db.get(UserAccountMerge, yandex_only)).target_user_id, telegram)
            joined = await db.get(User, telegram)
            self.assertEqual((joined.phone, joined.email, joined.birthday), ("79180001122", "ivan@yandex.ru", "1990-05-17"))
        # the old Yandex session and a new Yandex login both land in the Telegram account
        old = self.client.get("/api/auth/yandex/account", headers={"Authorization": "Bearer " + created["token"]}).json()
        self.assertEqual((old["linked"], old["telegram_join"]), (True, False))
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"], telegram)

    async def test_yandex_account_with_an_email_password_is_not_joined_by_linking(self):
        created = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        async with self.sessions() as db:
            db.add(EmailIdentity(user_id=created["user"]["id"], email="ivan@yandex.ru", password_hash="x"))
            await db.commit()
        telegram = await self.add_user(telegram_id=555, full_name="Я")
        headers = {"Authorization": "Bearer " + deps.create_token(telegram)}
        response = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin(headers, link=True)}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual([i.user_id for i in await self.identities()], [created["user"]["id"]])

    async def test_linking_with_different_phone_and_email_does_not_join(self):
        created = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        telegram = await self.add_user(telegram_id=555, full_name="Другой", phone="+70000000000")
        headers = {"Authorization": "Bearer " + deps.create_token(telegram)}
        response = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin(headers, link=True)}, headers=headers)
        self.assertEqual(response.status_code, 409)
        self.assertEqual([i.user_id for i in await self.identities()], [created["user"]["id"]])

    # ---- joining by the same confirmed email
    async def test_new_yandex_with_the_email_of_an_email_login_goes_to_that_account(self):
        email_user = await self.add_user(telegram_id=-77, full_name="Почта")
        async with self.sessions() as db:
            db.add(EmailIdentity(user_id=email_user, email="ivan@yandex.ru", password_hash="x"))
            await db.commit()
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertEqual((result["status"], result["user"]["id"]), ("ok", email_user))
        self.assertEqual(len(await self.users()), 1)
        (identity,) = await self.identities()
        self.assertEqual((identity.user_id, identity.email, identity.phone), (email_user, "ivan@yandex.ru", "+79180001122"))
        self.assertEqual(self.sent, [])

    async def test_earlier_yandex_account_joins_an_email_login_with_the_same_email(self):
        yandex_only = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"]
        email_user = await self.add_user(telegram_id=-77, full_name="Почта")
        async with self.sessions() as db:
            db.add(EmailIdentity(user_id=email_user, email="ivan@yandex.ru", password_hash="x"))
            await db.commit()
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertEqual(result["user"]["id"], email_user)
        async with self.sessions() as db:
            self.assertEqual((await db.get(UserAccountMerge, yandex_only)).target_user_id, email_user)
        self.assertEqual([i.user_id for i in await self.identities()], [email_user])

    async def test_different_email_does_not_join(self):
        email_user = await self.add_user(telegram_id=-77, full_name="Почта")
        async with self.sessions() as db:
            db.add(EmailIdentity(user_id=email_user, email="other@mail.ru", password_hash="x"))
            await db.commit()
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()
        self.assertNotEqual(result["user"]["id"], email_user)

    # ---- an earlier Yandex-only account meets a Telegram account with the same phone
    async def test_earlier_yandex_account_joins_telegram_with_the_same_phone_after_a_code(self):
        yandex_only = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"]
        async with self.sessions() as db:
            db.add(Business(owner_id=yandex_only, name="Мой бизнес", business_type="beauty"))
            await db.commit()
        telegram = await self.add_user(telegram_id=777, full_name="Иван", phone="89180001122")
        ticket = self.begin()
        self.assertEqual(self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket}).json(),
                         {"status": "code_required", "account": "existing"})
        (target, code), = self.sent
        self.assertEqual(target, telegram)
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "code": code}).json()
        self.assertEqual((result["user"]["id"], result["role"]), (telegram, "admin"))
        async with self.sessions() as db:
            self.assertEqual((await db.execute(select(Business.owner_id))).scalar_one(), telegram)
        self.assertEqual([i.user_id for i in await self.identities()], [telegram])

    async def test_signed_in_to_that_telegram_the_join_needs_no_code(self):
        yandex_only = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"]
        telegram = await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        headers = {"Authorization": "Bearer " + deps.create_token(telegram)}
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}, headers=headers).json()
        self.assertEqual(result["user"]["id"], telegram)
        self.assertEqual(self.sent, [])
        async with self.sessions() as db:
            self.assertIsNotNone(await db.get(UserAccountMerge, yandex_only))

    async def test_earlier_yandex_account_may_decline_and_sign_in_as_before(self):
        yandex_only = self.client.post("/api/auth/yandex/exchange", json={"ticket": self.begin()}).json()["user"]["id"]
        await self.add_user(telegram_id=777, full_name="Иван", phone="+79180001122")
        ticket = self.begin()
        self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket})
        result = self.client.post("/api/auth/yandex/exchange", json={"ticket": ticket, "separate": True}).json()
        self.assertEqual(result["user"]["id"], yandex_only)
        self.assertEqual([i.user_id for i in await self.identities()], [yandex_only])

    async def test_link_start_requires_sign_in(self):
        self.assertEqual(self.client.post("/api/auth/yandex/link/start").status_code, 401)


if __name__ == "__main__":
    unittest.main()
