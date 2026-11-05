"""ИИ-продавец бронирует площадку по правилам страницы записи: те же окна, цена и предоплата."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, time, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, User, Business, Service, Employee, VenueTimePricing, Booking
from web.api.sales.tools import ServGoTools, PriceArgs, AvailabilityArgs, BookingArgs


def next_monday() -> date:
    d = date.today() + timedelta(days=7)
    return d - timedelta(days=d.weekday())


class SalesVenueRulesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=99887766, full_name='Владелец')
            db.add(owner); await db.flush()
            venue = Business(owner_id=owner.id, name='Студия звукозаписи', business_type='venue_rental', is_active=True,
                             working_hours_start=time(9), working_hours_end=time(23),
                             prepayment_enabled=True, prepayment_percent=50,
                             sales_agent_settings={'policy': {}})
            db.add(venue); await db.flush()
            room = Employee(business_id=venue.id, name='Зал A', is_active=True, solo_price_per_hour=800)
            db.add(room); await db.flush()
            db.add_all([
                VenueTimePricing(employee_id=room.id, time_from=time(9), time_to=time(18), price_per_hour=1000, day_type='all'),
                VenueTimePricing(employee_id=room.id, time_from=time(18), time_to=time(23), price_per_hour=1500, day_type='all'),
            ])
            rent = Service(business_id=venue.id, name='Аренда 2 часа', duration_minutes=120, price=0, is_active=True)
            db.add(rent); await db.flush()
            self.ids = SimpleNamespace(venue=venue.id, room=room.id, rent=rent.id)
            await db.commit()
        self.day = next_monday()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def tools(self, db):
        business = await db.get(Business, self.ids.venue)
        return ServGoTools(db, business, None)

    async def test_quote_uses_time_pricing_like_booking_page(self):
        async with self.sessions() as db:
            tools = await self.tools(db)
            quote = await tools.quote(PriceArgs(service_id=self.ids.rent, employee_id=self.ids.room,
                                                date=self.day, time='17:00'))
        self.assertTrue(quote['booking_supported'])
        self.assertEqual(quote['amount'], 2500)          # 1000 + 1500
        self.assertEqual(quote['prepayment_amount'], 1250)
        self.assertEqual(quote['duration_minutes'], 120)

    async def test_availability_comes_from_booking_page_slots(self):
        async with self.sessions() as db:
            tools = await self.tools(db)
            result = await tools.availability(AvailabilityArgs(service_id=self.ids.rent, date=self.day))
        times = {s['time'] for s in result['slots']}
        self.assertIn('17:00', times)
        self.assertEqual(result['duration_minutes'], 120)

    async def test_ai_booking_is_a_regular_booking_with_same_prepayment(self):
        async with self.sessions() as db:
            tools = await self.tools(db)
            args = BookingArgs(service_id=self.ids.rent, employee_id=self.ids.room, date=self.day, time='17:00')
            prepared = await tools.prepare_booking(args)
            self.assertEqual(prepared['summary']['amount'], 2500)
            self.assertEqual(prepared['summary']['prepayment_amount'], 1250)
            confirmation = SimpleNamespace(client_name='Клиент', client_phone='+79990000000',
                                           personal_data_consent=False, booking_terms_accepted=False)
            ids, url = await tools.book(prepared, confirmation)
            await db.commit()
            booking = (await db.execute(select(Booking).where(Booking.id == ids[0]))).scalar_one()
        self.assertEqual(booking.status, 'pending_prepayment')
        self.assertEqual(booking.prepayment_amount, 1250)
        self.assertTrue(url.startswith('/b/'))

    async def test_solo_format(self):
        async with self.sessions() as db:
            tools = await self.tools(db)
            quote = await tools.quote(PriceArgs(service_id=self.ids.rent, employee_id=self.ids.room,
                                                date=self.day, time='10:00', solo=True))
        self.assertEqual(quote['amount'], 1600)          # 800 × 2 ч


if __name__ == '__main__':
    unittest.main()
