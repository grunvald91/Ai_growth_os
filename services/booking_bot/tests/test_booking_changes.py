"""Смена услуги в записи и «завершить сейчас»: правила, освобождение времени, права."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.booking_changes import (
    BookingChangeError, actual_end_time, change_booking_service, finish_visit_now,
)
from database.booking_safety import BookingConflictError, ensure_resource_available
from database.models import Base, Booking, Business, Employee, Service, User
from web.api.database import get_db
from web.api.deps import get_current_admin, get_current_user
from web.api.routes import admin as web_admin
from web.api.routes import master as web_master

TODAY = date.today() + timedelta(days=1)


def utc_for_local(day: date, hhmm: str) -> datetime:
    """Наивное UTC-время, соответствующее московскому моменту day hh:mm."""
    h, m = map(int, hhmm.split(':'))
    local = datetime(day.year, day.month, day.day, h, m, tzinfo=ZoneInfo('Europe/Moscow'))
    return local.astimezone(ZoneInfo('UTC')).replace(tzinfo=None)


class BookingChangesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=1001, full_name='Владелец')
            stranger = User(telegram_id=1003, full_name='Чужой владелец')
            client = User(telegram_id=1002, full_name='Клиент', phone='+79180000001')
            client2 = User(telegram_id=1004, full_name='Другой клиент', phone='+79180000002')
            db.add_all([owner, stranger, client, client2])
            await db.flush()
            biz = Business(owner_id=owner.id, name='Авто точка', business_type='auto_service')
            other = Business(owner_id=stranger.id, name='Чужой', business_type='auto_service')
            db.add_all([biz, other])
            await db.flush()
            self.svc60 = Service(business_id=biz.id, name='Сход-развал перед', duration_minutes=60, price=1700)
            self.svc90 = Service(business_id=biz.id, name='Сход-развал перед+зад', duration_minutes=90, price=2500)
            self.svc30 = Service(business_id=biz.id, name='Диагностика', duration_minutes=30, price=500)
            self.extra = Service(business_id=biz.id, name='Мойка', duration_minutes=15, price=300, is_additional=True)
            self.foreign = Service(business_id=other.id, name='Чужая услуга', duration_minutes=30, price=1)
            db.add_all([self.svc60, self.svc90, self.svc30, self.extra, self.foreign])
            await db.flush()
            self.master = Employee(business_id=biz.id, name='Сослан', telegram_id=5551, is_active=True)
            self.other_master = Employee(business_id=biz.id, name='Илья', telegram_id=5552, is_active=True)
            db.add_all([self.master, self.other_master])
            await db.flush()

            def booking(start, svc, employee, who=client, **kw):
                return Booking(
                    business_id=biz.id, client_id=who.id, service_id=svc.id, employee_id=employee.id,
                    booking_date=TODAY, booking_time=start, status='confirmed',
                    resource_duration_minutes=svc.duration_minutes, occupies_resource=True, **kw)

            self.a = booking(time(10, 0), self.svc60, self.master)
            self.b = booking(time(11, 0), self.svc60, self.master, client2)   # другой клиент сразу после A
            self.c = booking(time(10, 0), self.svc60, self.other_master)
            db.add_all([self.a, self.b, self.c])
            await db.commit()
            self.biz_id, self.owner_id, self.stranger_id = biz.id, owner.id, stranger.id
            self.a_id, self.b_id, self.c_id = self.a.id, self.b.id, self.c.id
            self.master_id, self.other_master_id = self.master.id, self.other_master.id
            self.svc = {k: v.id for k, v in dict(s60=self.svc60, s90=self.svc90, s30=self.svc30,
                                                 extra=self.extra, foreign=self.foreign).items()}

        self.current_user_id = None
        app = FastAPI()
        app.include_router(web_admin.router, prefix='/admin')
        app.include_router(web_master.router, prefix='/master')

        async def current_user():
            async with self.sessions() as db:
                return await db.get(User, self.current_user_id)

        async def session():
            async with self.sessions() as db:
                yield db

        app.dependency_overrides[get_current_user] = current_user
        app.dependency_overrides[get_current_admin] = current_user
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await self.engine.dispose()

    async def _load(self, booking_id):
        async with self.sessions() as db:
            return await db.get(Booking, booking_id)

    # ---- завершить сейчас ----

    async def test_finish_frees_time_from_that_moment(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            await finish_visit_now(db, booking)
            await db.commit()
        async with self.sessions() as db:
            # Мастер свободен сразу: можно записать нового клиента на 10:30 (в середине плановой услуги).
            await ensure_resource_available(db, employee_id=self.master_id, booking_date=TODAY,
                                            booking_time=time(10, 30), duration_minutes=30)
            # А вот на 11:00 по-прежнему стоит запись B.
            with self.assertRaises(BookingConflictError):
                await ensure_resource_available(db, employee_id=self.master_id, booking_date=TODAY,
                                                booking_time=time(10, 45), duration_minutes=30)
        done = await self._load(self.a_id)
        self.assertEqual(done.status, 'completed')
        self.assertIsNotNone(done.finished_at)

    async def test_finish_refuses_cancelled_and_already_completed(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            booking.status = 'cancelled'
            with self.assertRaises(BookingChangeError):
                await finish_visit_now(db, booking)
            booking.status = 'completed'
            with self.assertRaises(BookingChangeError) as ctx:
                await finish_visit_now(db, booking)
            self.assertEqual(ctx.exception.status_code, 400)

    async def test_actual_end_time_only_when_finished_early(self):
        booking = Booking(booking_date=TODAY, booking_time=time(10, 0), status='completed',
                          resource_duration_minutes=60, finished_at=utc_for_local(TODAY, '10:20'))
        self.assertEqual(actual_end_time(booking), '10:20')
        booking.finished_at = utc_for_local(TODAY, '11:30')   # завершили позже плана
        self.assertIsNone(actual_end_time(booking))
        booking.finished_at = utc_for_local(TODAY, '09:40')   # раньше начала → нулевая длина
        self.assertEqual(actual_end_time(booking), '10:00')
        booking.finished_at = None
        self.assertIsNone(actual_end_time(booking))
        booking.finished_at = utc_for_local(TODAY, '10:20')
        booking.status = 'confirmed'
        self.assertIsNone(actual_end_time(booking))

    async def test_admin_complete_stamps_finished_at_and_shows_early_end(self):
        self.current_user_id = self.owner_id
        res = self.client.patch(f'/admin/bookings/{self.a_id}/status', json={'status': 'completed'})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()['status'], 'completed')
        self.assertIsNotNone((await self._load(self.a_id)).finished_at)
        back = self.client.patch(f'/admin/bookings/{self.a_id}/status', json={'status': 'confirmed'})
        self.assertEqual(back.status_code, 200, back.text)
        self.assertIsNone((await self._load(self.a_id)).finished_at)

    async def test_master_finishes_only_own_booking(self):
        self.current_user_id = await self._make_master_user(5551)
        self.assertEqual(self.client.post(f'/master/bookings/{self.c_id}/finish').status_code, 404)
        self.assertEqual((await self._load(self.c_id)).status, 'confirmed')
        ok = self.client.post(f'/master/bookings/{self.a_id}/finish')
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual((await self._load(self.a_id)).status, 'completed')
        again = self.client.post(f'/master/bookings/{self.a_id}/finish')
        self.assertEqual(again.status_code, 400)

    async def _make_master_user(self, telegram_id):
        async with self.sessions() as db:
            user = User(telegram_id=telegram_id, full_name='Мастер')
            db.add(user)
            await db.commit()
            return user.id

    # ---- смена услуги ----

    async def test_change_to_shorter_service_updates_occupancy(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            await change_booking_service(db, booking, self.svc['s30'])
            await db.commit()
        changed = await self._load(self.a_id)
        self.assertEqual((changed.service_id, changed.resource_duration_minutes), (self.svc['s30'], 30))
        self.assertEqual(changed.version, 2)

    async def test_change_to_longer_service_is_refused_when_next_slot_busy(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            with self.assertRaises(BookingChangeError) as ctx:
                await change_booking_service(db, booking, self.svc['s90'])
            self.assertIn('перенесите соседнюю', ctx.exception.detail)
        self.assertEqual((await self._load(self.a_id)).service_id, self.svc['s60'])

    async def test_change_to_longer_service_allowed_when_time_is_free(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.c_id)   # у Ильи после записи свободно
            await change_booking_service(db, booking, self.svc['s90'])
            await db.commit()
        self.assertEqual((await self._load(self.c_id)).resource_duration_minutes, 90)

    async def test_change_refuses_foreign_inactive_additional_and_same_service(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            with self.assertRaises(BookingChangeError) as ctx:
                await change_booking_service(db, booking, self.svc['foreign'])
            self.assertEqual(ctx.exception.status_code, 404)
            with self.assertRaises(BookingChangeError):
                await change_booking_service(db, booking, self.svc['extra'])
            with self.assertRaises(BookingChangeError) as same:
                await change_booking_service(db, booking, self.svc['s60'])
            self.assertEqual(same.exception.status_code, 400)
            (await db.get(Service, self.svc['s30'])).is_active = False
            with self.assertRaises(BookingChangeError):
                await change_booking_service(db, booking, self.svc['s30'])

    async def test_change_works_for_prepaid_booking_and_keeps_the_prepayment(self):
        for status in ('prepayment_confirmed', 'prepayment_sent', 'pending_prepayment'):
            async with self.sessions() as db:
                booking = await db.get(Booking, self.c_id)
                booking.status, booking.prepayment_amount = status, 500
                booking.service_id = self.svc['s60']
                booking.resource_duration_minutes = 60
                await db.commit()
            self.current_user_id = self.owner_id
            res = self.client.patch(f'/admin/bookings/{self.c_id}/service', json={'service_id': self.svc['s90']})
            self.assertEqual(res.status_code, 200, (status, res.text))
            body = res.json()
            self.assertEqual((body['service_name'], body['price'], body['prepayment_amount'], body['status']),
                             ('Сход-развал перед+зад', 2500, 500, status))

    async def test_change_refuses_rental_and_cancelled(self):
        async with self.sessions() as db:
            booking = await db.get(Booking, self.a_id)
            booking.duration_minutes = 60   # признак аренды
            with self.assertRaises(BookingChangeError):
                await change_booking_service(db, booking, self.svc['s30'])
            booking.duration_minutes = None
            booking.status = 'cancelled'
            with self.assertRaises(BookingChangeError):
                await change_booking_service(db, booking, self.svc['s30'])

    async def test_change_works_for_venue_rental_packages(self):
        async with self.sessions() as db:
            biz = await db.get(Business, self.biz_id)
            biz.business_type = 'venue_rental'
            await db.commit()
        self.current_user_id = self.owner_id
        res = self.client.patch(f'/admin/bookings/{self.c_id}/service', json={'service_id': self.svc['s90']})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((await self._load(self.c_id)).resource_duration_minutes, 90)

    async def test_master_finishes_prepaid_booking(self):
        async with self.sessions() as db:
            (await db.get(Booking, self.a_id)).status = 'prepayment_confirmed'
            await db.commit()
        self.current_user_id = await self._make_master_user(5551)
        self.assertEqual(self.client.post(f'/master/bookings/{self.a_id}/finish').status_code, 200)
        self.assertEqual((await self._load(self.a_id)).status, 'completed')

    async def test_admin_endpoint_changes_service_and_checks_ownership(self):
        url = f'/admin/bookings/{self.a_id}/service'
        self.current_user_id = self.stranger_id
        self.assertEqual(self.client.patch(url, json={'service_id': self.svc['s30']}).status_code, 403)
        self.current_user_id = self.owner_id
        res = self.client.patch(url, json={'service_id': self.svc['s30']})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual((res.json()['service_id'], res.json()['service_name']),
                         (self.svc['s30'], 'Диагностика'))
        refused = self.client.patch(url, json={'service_id': self.svc['s90']})
        self.assertEqual(refused.status_code, 409)   # 10:00–11:30 упирается в запись B на 11:00
        self.assertEqual(self.client.patch(f'/admin/bookings/{self.b_id}/service',
                                           json={'service_id': self.svc['foreign']}).status_code, 404)

    async def test_master_endpoint_changes_own_booking_only_and_lists_services_without_prices(self):
        self.current_user_id = await self._make_master_user(5551)
        schedule = self.client.get('/master/schedule', params={
            'date_from': TODAY.isoformat(), 'date_to': TODAY.isoformat()}).json()
        names = {sv['name'] for sv in schedule['services']}
        self.assertIn('Диагностика', names)
        self.assertNotIn('Чужая услуга', names)
        self.assertTrue(all('price' not in sv for sv in schedule['services']))
        self.assertEqual(self.client.patch(f'/master/bookings/{self.c_id}/service',
                                           json={'service_id': self.svc['s30']}).status_code, 404)
        ok = self.client.patch(f'/master/bookings/{self.a_id}/service', json={'service_id': self.svc['s30']})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual((await self._load(self.a_id)).service_id, self.svc['s30'])


if __name__ == '__main__':
    unittest.main()
