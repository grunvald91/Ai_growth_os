"""Email account -> Telegram merge keeps working after the shared data move was extracted."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from datetime_utils import utc_now
from database.account_link import merge_email_into_telegram
from database.models import Base, Business, EmailIdentity, TelegramLinkRequest, User, UserAccountMerge


class EmailMergeRegressionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_email_account_moves_into_telegram(self):
        async with self.sessions() as db:
            source = User(telegram_id=-9, full_name="Почта", phone="+79180000000")
            target = User(telegram_id=777, full_name="Telegram")
            db.add_all([source, target]); await db.flush()
            db.add(EmailIdentity(user_id=source.id, email="a@b.ru", password_hash="x", session_version=3))
            db.add(Business(owner_id=source.id, name="Б", business_type="beauty"))
            request = TelegramLinkRequest(id="r" * 36, user_id=source.id, token_hash="t" * 64, email_session_version=3,
                                          telegram_user_id=target.id, telegram_confirmed=True,
                                          expires_at=utc_now() + timedelta(minutes=5))
            db.add(request); await db.commit()
            identity = await merge_email_into_telegram(db, request, source.id)
            await db.commit()
            self.assertEqual((identity.user_id, identity.session_version), (target.id, 4))
            self.assertEqual(await db.scalar(select(Business.owner_id)), target.id)
            self.assertEqual((await db.get(UserAccountMerge, source.id)).target_user_id, target.id)
            joined = await db.get(User, target.id)
            self.assertEqual((joined.email, joined.phone), ("a@b.ru", "+79180000000"))
            self.assertTrue(request.consumed)


if __name__ == "__main__":
    unittest.main()
