"""Read-only schedule for the studio dispatcher: who may ask, what is returned, what is not."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, datetime, time, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, Booking, Business, Employee, Service, User
from web.api.database import get_db
from web.api.routes import studio_schedule, studio_sso

SSO = 's' * 40
KEY = 'k' * 40


class StudioScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = mock.patch.dict(os.environ, {'STUDIO_SSO_SECRET': SSO, 'STUDIO_INTERNAL_KEY': KEY})
        self.env.start()
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.today = datetime.now(ZoneInfo('Europe/Moscow')).date()
        self.day = self.today + timedelta(days=3)
        async with self.sessions() as db:
            owner = User(telegram_id=1, full_name='Владелец')
            stranger = User(telegram_id=2, full_name='Чужой')
            client_a = User(telegram_id=3, full_name='Анна Петрова', phone='+79990000001', email='anna@example.com')
            client_b = User(telegram_id=4, full_name='Борис Иванов', phone='+79990000002')
            db.add_all([owner, stranger, client_a, client_b]); await db.flush()
            business = Business(owner_id=owner.id, name='Салон', business_type='salon', is_active=True,
                                working_hours_start=time(10, 0), working_hours_end=time(14, 0),
                                break_start=time(12, 0), break_end=time(13, 0), booking_interval_minutes=60)
            other = Business(owner_id=owner.id, name='Другой', business_type='salon', is_active=True,
                             working_hours_start=time(10, 0), working_hours_end=time(14, 0))
            db.add_all([business, other]); await db.flush()
            service = Service(business_id=business.id, name='Стрижка', duration_minutes=60, price=1500)
            other_service = Service(business_id=other.id, name='Чужая услуга', duration_minutes=60)
            master = Employee(business_id=business.id, name='Мария', role='master', is_active=True)
            admin = Employee(business_id=business.id, name='Админ', role='administrator', is_active=True)
            db.add_all([service, other_service, master, admin]); await db.flush()
            db.add_all([
                Booking(business_id=business.id, client_id=client_a.id, service_id=service.id, employee_id=master.id,
                        booking_date=self.day, booking_time=time(10, 0), status='confirmed',
                        resource_duration_minutes=60, estimated_price=1500, notes='секретная заметка'),
                Booking(business_id=business.id, client_id=client_b.id, service_id=service.id, employee_id=master.id,
                        booking_date=self.day, booking_time=time(11, 0), status='cancelled', resource_duration_minutes=60),
                Booking(business_id=business.id, client_id=client_b.id, service_id=service.id, employee_id=master.id,
                        booking_date=self.day + timedelta(days=30), booking_time=time(10, 0), status='confirmed',
                        resource_duration_minutes=60),
                Booking(business_id=other.id, client_id=client_a.id, service_id=other_service.id,
                        booking_date=self.day, booking_time=time(10, 0), status='confirmed', resource_duration_minutes=60),
            ])
            await db.commit()
            self.owner_id, self.stranger_id = owner.id, stranger.id
            self.business_id, self.other_id, self.master_id = business.id, other.id, master.id
        app = FastAPI()
        app.include_router(studio_schedule.router)

        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close(); await self.engine.dispose(); self.env.stop()

    def ask(self, user_id=None, business_id=None, key=KEY, **extra):
        params = {'business_id': business_id or self.business_id, 'user_id': user_id or self.owner_id,
                  'date_from': self.day.isoformat(), 'date_to': self.day.isoformat(), **extra}
        if user_id == 0:
            del params['user_id']
        return self.client.get('/internal/studio/schedule', headers={'X-Studio-Key': key}, params=params)

    async def test_returns_the_days_bookings_with_client_contacts(self):
        body = self.ask().json()
        self.assertEqual([item['time'] for item in body['bookings']], ['10:00', '11:00'])
        first = body['bookings'][0]
        self.assertEqual((first['client_name'], first['client_phone'], first['client_email']),
                         ('Анна Петрова', '+79990000001', 'anna@example.com'))
        self.assertEqual((first['service'], first['resource'], first['price'], first['duration_minutes']),
                         ('Стрижка', 'Мария', 1500, 60))

    async def test_bookings_carry_client_id_and_creation_time(self):
        body = self.ask().json()['bookings']
        self.assertTrue(all(isinstance(item['client_id'], int) for item in body))
        self.assertEqual(len({item['client_id'] for item in body}), 2)
        self.assertTrue(all(item['created_at'] for item in body))
        datetime.fromisoformat(body[0]['created_at'])

    async def test_cancelled_bookings_are_included_with_their_status(self):
        statuses = {item['time']: item['status'] for item in self.ask().json()['bookings']}
        self.assertEqual(statuses, {'10:00': 'confirmed', '11:00': 'cancelled'})

    async def test_only_the_asked_period_and_only_this_business(self):
        body = self.ask().json()
        self.assertEqual({item['date'] for item in body['bookings']}, {self.day.isoformat()})
        self.assertNotIn('Чужая услуга', [item['service'] for item in body['bookings']])
        wide = self.ask(date_to=(self.day + timedelta(days=30)).isoformat()).json()
        self.assertEqual(len(wide['bookings']), 3)

    async def test_notes_and_telegram_ids_are_never_sent(self):
        text = self.ask().text
        self.assertNotIn('секретная заметка', text)
        self.assertNotIn('telegram', text.lower())

    async def test_wrong_or_missing_key_is_refused(self):
        self.assertEqual(self.ask(key='x' * 40).status_code, 403)
        self.assertEqual(self.ask(key='').status_code, 403)

    async def test_stranger_and_other_business_are_refused(self):
        self.assertEqual(self.ask(user_id=self.stranger_id).status_code, 403)
        # the owner owns both businesses, but a stranger asking for the other one is refused as well
        self.assertEqual(self.ask(user_id=self.stranger_id, business_id=self.other_id).status_code, 403)

    async def test_without_a_person_the_key_alone_is_enough_for_a_linked_company(self):
        self.assertEqual(self.ask(user_id=0).status_code, 200)
        self.assertEqual(self.ask(user_id=0, key='x' * 40).status_code, 403)

    async def test_unknown_business_is_not_found(self):
        self.assertEqual(self.ask(user_id=0, business_id=9999).status_code, 404)

    async def test_off_without_secrets(self):
        with mock.patch.dict(os.environ, {'STUDIO_INTERNAL_KEY': ''}):
            self.assertEqual(self.ask().status_code, 503)

    async def test_reversed_period_is_rejected(self):
        response = self.ask(date_from=self.day.isoformat(), date_to=(self.day - timedelta(days=1)).isoformat())
        self.assertEqual(response.status_code, 422)

    async def test_resources_are_masters_not_administrators(self):
        self.assertEqual([item['name'] for item in self.ask().json()['resources']], ['Мария'])

    async def test_working_hours_and_today_come_from_the_platform(self):
        body = self.ask().json()
        self.assertEqual((body['working_hours'], body['break_hours'], body['step_minutes']), ('10:00–14:00', '12:00–13:00', 60))
        self.assertEqual(body['today'], self.today.isoformat())

    async def test_free_slots_use_the_platform_slot_logic(self):
        """10:00 is booked, 11:00 was cancelled (free again), 12:00 is the break, 13:00 is free."""
        body = self.ask(with_slots=True).json()
        self.assertEqual(len(body['free_slots']), 1)
        slot = body['free_slots'][0]
        self.assertEqual((slot['date'], slot['resource']), (self.day.isoformat(), 'Мария'))
        self.assertEqual(slot['times'], ['11:00', '13:00'])

    async def test_hall_with_a_minimum_rental_time_still_has_free_windows(self):
        """Step 60 minutes, minimum 120: windows of 120 minutes are looked for, not none at all."""
        async with self.sessions() as db:
            (await db.get(Employee, self.master_id)).min_booking_minutes = 120
            (await db.get(Business, self.business_id)).working_hours_end = time(18, 0)
            await db.commit()
        slot = self.ask(with_slots=True).json()['free_slots'][0]
        self.assertEqual(slot['times'], ['13:00', '14:00', '15:00', '16:00'])

    async def test_add_ons_are_marked_as_not_taking_time(self):
        async with self.sessions() as db:
            booking = (await db.scalars(select(Booking).where(Booking.status == 'confirmed').order_by(Booking.id))).first()
            booking.occupies_resource = False
            await db.commit()
        rows = {item['time']: item for item in self.ask().json()['bookings']}
        self.assertFalse(rows['10:00']['occupies_resource'])
        self.assertTrue(rows['11:00']['occupies_resource'])

    async def test_no_slots_unless_asked(self):
        self.assertEqual(self.ask().json()['free_slots'], [])

    async def test_it_changes_nothing(self):
        async with self.sessions() as db:
            before = await db.scalar(select(func.count()).select_from(Booking))
        self.ask(with_slots=True)
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Booking)), before)

    async def test_business_without_masters_gets_one_business_wide_row_per_day(self):
        async with self.sessions() as db:
            (await db.get(Employee, self.master_id)).is_active = False
            await db.commit()
        body = self.ask(with_slots=True).json()
        self.assertEqual(body['resources'], [])
        self.assertEqual(len(body['free_slots']), 1)
        self.assertIsNone(body['free_slots'][0]['resource_id'])


if __name__ == '__main__':
    unittest.main()
