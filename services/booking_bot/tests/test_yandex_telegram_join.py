"""Signed in through Yandex (no Telegram), then signed in through Telegram: the accounts join automatically."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Business, EmailIdentity, User, UserAccountMerge, YandexIdentity
from web.api import deps
from web.api.database import get_db
from web.api.routes import auth

WIDGET = {"id": 777, "first_name": "Иван", "auth_date": 1, "hash": "h"}


class YandexTelegramJoinTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def no_cookie(response, auth_dict):
            return None

        self._originals = (auth.verify_telegram_login, auth._set_shared_auth_cookie)
        auth.verify_telegram_login = lambda payload, tokens: True
        auth._set_shared_auth_cookie = no_cookie
        app = FastAPI()
        app.include_router(auth.router, prefix="/auth")

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        auth.verify_telegram_login, auth._set_shared_auth_cookie = self._originals
        auth._pending_codes.clear()
        self.client.close()
        await self.engine.dispose()

    async def yandex_user(self, *, email_password=False, phone="+79180001122"):
        async with self.sessions() as db:
            user = User(telegram_id=-123456, full_name="Иван Яндекс", email="ivan@yandex.ru", phone=phone, birthday="1990-05-17")
            db.add(user)
            await db.flush()
            db.add(YandexIdentity(yandex_id="1000", user_id=user.id, login="ivan", email="ivan@yandex.ru", phone=phone))
            db.add(Business(owner_id=user.id, name="Студия", business_type="beauty"))
            if email_password:
                db.add(EmailIdentity(user_id=user.id, email="ivan@yandex.ru", password_hash="x"))
            await db.commit()
            return user.id

    async def telegram_user(self, phone="79180001122", telegram_id=777):
        async with self.sessions() as db:
            user = User(telegram_id=telegram_id, full_name="Иван", phone=phone)
            db.add(user)
            await db.commit()
            return user.id

    def bearer(self, user_id):
        return {"Authorization": "Bearer " + deps.create_token(user_id)}

    async def row(self, model, key):
        async with self.sessions() as db:
            return await db.get(model, key)

    async def test_telegram_login_with_the_same_phone_while_signed_in_via_yandex_joins(self):
        yandex = await self.yandex_user()
        telegram = await self.telegram_user()  # bot stores the number without "+"
        result = self.client.post("/auth/telegram-login", json=WIDGET, headers=self.bearer(yandex)).json()
        self.assertEqual((result["user"]["id"], result["role"]), (telegram, "admin"))
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, telegram)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(Business.owner_id)), telegram)
            joined = await db.get(User, telegram)
        self.assertEqual((joined.phone, joined.email, joined.birthday), ("79180001122", "ivan@yandex.ru", "1990-05-17"))
        self.assertEqual((await self.row(UserAccountMerge, yandex)).target_user_id, telegram)
        self.assertEqual(self.client.get("/auth/me", headers=self.bearer(yandex)).json()["id"], telegram)

    async def test_different_phone_is_not_joined(self):
        yandex = await self.yandex_user()
        telegram = await self.telegram_user(phone="+70000000000")
        result = self.client.post("/auth/telegram-login", json=WIDGET, headers=self.bearer(yandex)).json()
        self.assertEqual(result["user"]["id"], telegram)
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, yandex)
        self.assertIsNone(await self.row(UserAccountMerge, yandex))

    async def test_new_telegram_account_without_a_phone_is_not_joined(self):
        yandex = await self.yandex_user()
        result = self.client.post("/auth/telegram-login", json=WIDGET, headers=self.bearer(yandex)).json()
        self.assertNotEqual(result["user"]["id"], yandex)
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, yandex)

    async def test_without_a_yandex_session_nothing_is_joined(self):
        yandex = await self.yandex_user()
        await self.telegram_user()
        self.client.post("/auth/telegram-login", json=WIDGET)
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, yandex)
        self.assertIsNone(await self.row(UserAccountMerge, yandex))

    async def test_yandex_account_with_email_password_is_left_alone(self):
        yandex = await self.yandex_user(email_password=True)
        await self.telegram_user()
        result = self.client.post("/auth/telegram-login", json=WIDGET, headers=self.bearer(yandex))
        self.assertEqual(result.status_code, 200)
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, yandex)

    async def test_telegram_account_with_another_yandex_still_signs_in_without_joining(self):
        yandex = await self.yandex_user()
        telegram = await self.telegram_user()
        async with self.sessions() as db:
            db.add(YandexIdentity(yandex_id="2000", user_id=telegram, login="other"))
            await db.commit()
        result = self.client.post("/auth/telegram-login", json=WIDGET, headers=self.bearer(yandex))
        self.assertEqual((result.status_code, result.json()["user"]["id"]), (200, telegram))
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, yandex)

    async def test_phone_code_from_telegram_while_signed_in_via_yandex_joins_too(self):
        yandex = await self.yandex_user(phone="+79990001122")
        telegram = await self.telegram_user(phone="+79990001122")
        auth._pending_codes["+79990001122"] = ("123456", time.time())
        result = self.client.post("/auth/verify", json={"phone": "+79990001122", "code": "123456"},
                                  headers=self.bearer(yandex)).json()
        self.assertEqual(result["user"]["id"], telegram)
        self.assertEqual((await self.row(YandexIdentity, "1000")).user_id, telegram)


if __name__ == "__main__":
    unittest.main()
