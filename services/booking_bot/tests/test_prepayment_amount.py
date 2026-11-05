"""Предоплата считается от той же цены, что видит клиент на странице записи."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, User, Business, Service, Employee, VenueTimePricing
from web.api.database import get_db
from web.api.routes import client

MONDAY = '2030-01-07'


class PrepaymentAmountTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=55667788, full_name='Владелец')
            db.add(owner); await db.flush()
            venue = Business(owner_id=owner.id, name='Студия звукозаписи', business_type='venue_rental', is_active=True,
                             working_hours_start=time(9), working_hours_end=time(23),
                             prepayment_enabled=True, prepayment_percent=50)
            salon = Business(owner_id=owner.id, name='Салон', business_type='beauty', is_active=True,
                             working_hours_start=time(9), working_hours_end=time(21),
                             prepayment_enabled=True, prepayment_percent=50)
            db.add_all([venue, salon]); await db.flush()
            self.venue_id, self.salon_id = venue.id, salon.id
            room = Employee(business_id=venue.id, name='Зал A', is_active=True, solo_price_per_hour=800)
            master = Employee(business_id=salon.id, name='Мастер', is_active=True)
            db.add_all([room, master]); await db.flush()
            self.room_id, self.master_id = room.id, master.id
            db.add_all([
                VenueTimePricing(employee_id=room.id, time_from=time(9), time_to=time(18), price_per_hour=1000, day_type='all'),
                VenueTimePricing(employee_id=room.id, time_from=time(18), time_to=time(23), price_per_hour=1500, day_type='all'),
            ])
            rent = Service(business_id=venue.id, name='Аренда 2 часа', duration_minutes=120, price=0, is_active=True)
            cut = Service(business_id=salon.id, name='Стрижка', duration_minutes=60, price=2000, is_active=True)
            db.add_all([rent, cut]); await db.flush()
            self.rent_id, self.cut_id = rent.id, cut.id
            await db.commit()
        app = FastAPI()
        app.include_router(client.router)

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close(); await self.engine.dispose()

    def book(self, business_id, service_id, employee_id, time_str, **extra):
        payload = {'business_id': business_id, 'service_ids': [service_id], 'employee_id': employee_id,
                   'date': MONDAY, 'time': time_str, 'client_name': 'Клиент', 'client_phone': '+79990000000', **extra}
        res = self.client.post('/bookings', json=payload)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()[0]

    def test_venue_uses_time_pricing_shown_to_client(self):
        # 17:00–19:00: час по 1000 + час по 1500 = 2500 → 50% = 1250.
        preview = self.client.get(f'/businesses/{self.venue_id}/calculate-price',
                                  params={'employee_id': self.room_id, 'time': '17:00', 'duration': 120, 'date': MONDAY}).json()
        self.assertEqual(preview['total'], 2500)
        booking = self.book(self.venue_id, self.rent_id, self.room_id, '17:00')
        self.assertEqual(booking['status'], 'pending_prepayment')
        self.assertEqual(booking['prepayment_amount'], 1250)

    def test_venue_solo_price(self):
        # Соло: 800 ₽/ч × 2 ч = 1600 → 50% = 800.
        booking = self.book(self.venue_id, self.rent_id, self.room_id, '10:00', is_solo_booking=True)
        self.assertEqual(booking['prepayment_amount'], 800)

    def test_regular_business_uses_service_price(self):
        booking = self.book(self.salon_id, self.cut_id, self.master_id, '10:00')
        self.assertEqual(booking['status'], 'pending_prepayment')
        self.assertEqual(booking['prepayment_amount'], 1000)


if __name__ == '__main__':
    unittest.main()
