"""Opening the admin schedule must not resend «Новая подтверждённая запись».

Regression: GET /admin/bookings dispatched booking_created for every listed
booking, and SchedulePage polls it every 15 s, so owners, masters and clients
got the same notification over and over. The event belongs to POST only.
"""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, time
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, User, Business, Service, Employee, Booking
from web.api.database import get_db
from web.api.routes import admin
from web.api.deps import get_current_admin


class AdminBookingNotificationsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=11223344, full_name='Владелец')
            client = User(telegram_id=55667788, full_name='Клиент', phone='+79990000000')
            db.add_all([owner, client]); await db.flush(); self.owner_id = owner.id
            business = Business(owner_id=owner.id, name='Студия', business_type='beauty', is_active=True,
                                working_hours_start=time(9), working_hours_end=time(21))
            db.add(business); await db.flush(); self.business_id = business.id
            cut = Service(business_id=business.id, name='Стрижка', duration_minutes=60, price=2000, is_active=True)
            master = Employee(business_id=business.id, name='Мастер', is_active=True)
            db.add_all([cut, master]); await db.flush()
            self.service_id, self.master_id = cut.id, master.id
            db.add(Booking(business_id=business.id, client_id=client.id, service_id=cut.id,
                           employee_id=master.id, booking_date=date(2030, 1, 7),
                           booking_time=time(12), status='confirmed'))
            await db.commit()
        app = FastAPI()
        app.include_router(admin.router, prefix='/admin')

        async def owner_user():
            async with self.sessions() as db:
                return await db.get(User, self.owner_id)

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_current_admin] = owner_user
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close(); await self.engine.dispose()

    def test_listing_bookings_sends_no_notifications(self):
        with patch('bot.services.notification_events.dispatch_booking_event', new=AsyncMock()) as dispatch:
            for _ in range(3):
                res = self.client.get('/admin/bookings', params={'business_id': self.business_id})
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(len(res.json()), 1)
        dispatch.assert_not_awaited()

    def test_manual_booking_notifies_once_per_created_visit(self):
        with patch('bot.services.notification_events.dispatch_booking_event', new=AsyncMock()) as dispatch:
            res = self.client.post('/admin/bookings', json={
                'business_id': self.business_id, 'service_id': self.service_id,
                'employee_id': self.master_id, 'date': '2030-01-08', 'time': '10:00',
                'client_name': 'Новый клиент', 'client_phone': '+79991112233', 'repeat_weeks': 2})
            self.assertEqual(res.status_code, 200, res.text)
            created_ids = [item['id'] for item in res.json()]
        self.assertEqual(len(created_ids), 2)
        self.assertEqual(dispatch.await_count, 2)
        notified = [call.args[0][0].id for call in dispatch.await_args_list]
        self.assertEqual(sorted(notified), sorted(created_ids))
        for call in dispatch.await_args_list:
            self.assertEqual(call.args[2], 'booking_created')


if __name__ == '__main__':
    unittest.main()
