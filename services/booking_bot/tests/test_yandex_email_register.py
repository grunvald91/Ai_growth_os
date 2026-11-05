"""Registering an email login with the address Yandex confirmed puts it on the Yandex account."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, EmailIdentity, User, YandexIdentity
from web.api.database import get_db
from web.api.routes import email_auth


class YandexEmailRegisterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._env = os.environ.get("SERVGO_EMAIL_AUTH_SECRET")
        os.environ["SERVGO_EMAIL_AUTH_SECRET"] = "s" * 40
        self.mail = []

        async def fake_send(to_email, subject, text_body):
            self.mail.append(text_body)
            return True

        self._originals = (email_auth.require_config, email_auth.send_email)
        email_auth.require_config = lambda: None
        email_auth.send_email = fake_send
        app = FastAPI()
        app.include_router(email_auth.router, prefix="/auth")

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        email_auth.require_config, email_auth.send_email = self._originals
        if self._env is None:
            os.environ.pop("SERVGO_EMAIL_AUTH_SECRET", None)
        else:
            os.environ["SERVGO_EMAIL_AUTH_SECRET"] = self._env
        self.client.close()
        await self.engine.dispose()

    async def yandex_account(self, email):
        async with self.sessions() as db:
            user = User(telegram_id=-5, full_name="Иван")
            db.add(user)
            await db.flush()
            db.add(YandexIdentity(yandex_id="1000", user_id=user.id, login="ivan", email=email))
            await db.commit()
            return user.id

    def register(self, email):
        start = self.client.post("/auth/email/start", json={"email": email, "password": "x" * 12,
                                                             "purpose": "register", "full_name": "Иван"}).json()
        code = self.mail[-1].split("Код: ")[1][:6]
        return self.client.post("/auth/email/verify", json={"challenge_id": start["challenge_id"], "code": code}).json()

    async def test_same_email_goes_to_the_yandex_account(self):
        yandex = await self.yandex_account("ivan@yandex.ru")
        result = self.register("Ivan@Yandex.ru")
        self.assertEqual(result["user"]["id"], yandex)
        async with self.sessions() as db:
            self.assertEqual(len((await db.execute(select(User))).scalars().all()), 1)
            self.assertEqual((await db.get(EmailIdentity, "ivan@yandex.ru")).user_id, yandex)
            self.assertEqual((await db.get(User, yandex)).email, "ivan@yandex.ru")

    async def test_other_email_creates_a_new_account(self):
        yandex = await self.yandex_account("ivan@yandex.ru")
        result = self.register("other@mail.ru")
        self.assertNotEqual(result["user"]["id"], yandex)


if __name__ == "__main__":
    unittest.main()
