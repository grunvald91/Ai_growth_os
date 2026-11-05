"""Reminder checker must survive an undelivered reminder.

Regression: when no channel delivered a client or staff reminder, the sender
called session.rollback(). A rollback expires every object loaded in the
shared session, so the checker crashed with MissingGreenlet on the next visit
("Error checking reminders" every 5 minutes) and no later reminder was sent.
"""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from bot.services import notifications
from database.models import Base, User, Business, Service, Booking


BLOCKED_CLIENT = 70000001
OK_CLIENT = 70000002


class ReminderCheckerRollbackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # Visit in 5 hours: inside the client 24h window, outside staff offsets.
        visit = datetime.now(ZoneInfo('Europe/Moscow')).replace(tzinfo=None, second=0, microsecond=0) + timedelta(hours=5)
        async with self.sessions() as db:
            owner = User(telegram_id=-1, full_name='Владелец')
            blocked = User(telegram_id=BLOCKED_CLIENT, full_name='Клиент А')
            ok = User(telegram_id=OK_CLIENT, full_name='Клиент Б')
            db.add_all([owner, blocked, ok]); await db.flush()
            business = Business(owner_id=owner.id, name='Шиномонтаж', business_type='tire_service',
                                owner_reminders_enabled=False, master_reminders_enabled=False)
            db.add(business); await db.flush()
            service = Service(business_id=business.id, name='Переобувка', duration_minutes=30, price=1500)
            db.add(service); await db.flush()
            first = Booking(client_id=blocked.id, business_id=business.id, service_id=service.id,
                            booking_date=visit.date(), booking_time=visit.time(), status='confirmed')
            second = Booking(client_id=ok.id, business_id=business.id, service_id=service.id,
                             booking_date=visit.date(), booking_time=(visit + timedelta(hours=1)).time(),
                             status='confirmed')
            db.add_all([first, second]); await db.commit()
            self.first_id, self.second_id = first.id, second.id

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_undelivered_reminder_does_not_stop_the_rest(self):
        async def send_message(chat_id, **_):
            if chat_id == BLOCKED_CLIENT:
                raise RuntimeError('Forbidden: bot was blocked by the user')

        bot = AsyncMock()
        bot.send_message.side_effect = send_message

        with patch.object(notifications, 'async_session_maker', self.sessions), \
                patch.object(notifications, 'send_booking_email', AsyncMock(return_value=False)), \
                self.assertNoLogs(notifications.logger, level='ERROR'):
            await notifications.check_and_send_reminders(bot)

        chats = [call.kwargs['chat_id'] for call in bot.send_message.await_args_list]
        self.assertEqual(chats, [BLOCKED_CLIENT, OK_CLIENT])
        async with self.sessions() as db:
            self.assertFalse((await db.get(Booking, self.first_id)).reminder_24h_sent)
            self.assertTrue((await db.get(Booking, self.second_id)).reminder_24h_sent)


if __name__ == '__main__':
    unittest.main()
