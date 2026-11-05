"""Личный кабинет мастера: кто считается мастером, что он видит и чего не видит."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, time, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import (
    Base, Booking, Business, Employee, EmployeeDayOff, EmailIdentity, Service, User, YandexIdentity,
)
from web.api.database import get_db
from web.api.deps import get_current_admin, get_current_user
from web.api.routes import admin as web_admin
from web.api.routes import master as web_master
from web.api.schemas import EmployeeOut

TODAY = date.today()


class MasterCabinetTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=1001, full_name='Владелец')
            client = User(telegram_id=1002, full_name='Клиент Иван', phone='+79180000001')
            db.add_all([owner, client])
            await db.flush()
            biz = Business(owner_id=owner.id, name='Авто точка', business_type='auto_service')
            db.add(biz)
            await db.flush()
            service = Service(business_id=biz.id, name='Сход-развал', duration_minutes=60, price=1700)
            db.add(service)
            await db.flush()
            self.sosl = Employee(business_id=biz.id, name='Сослан', telegram_id=5551,
                                 access_email='sosl@example.org', is_active=True)
            self.ilya = Employee(business_id=biz.id, name='Илья', access_email='ilya@example.org', is_active=True)
            self.gone = Employee(business_id=biz.id, name='Уволен', access_email='gone@example.org', is_active=False)
            db.add_all([self.sosl, self.ilya, self.gone])
            await db.flush()
            db.add_all([
                Booking(business_id=biz.id, client_id=client.id, service_id=service.id, employee_id=self.sosl.id,
                        booking_date=TODAY, booking_time=time(10, 0), status='confirmed', notes='Скрипит перед'),
                Booking(business_id=biz.id, client_id=client.id, service_id=service.id, employee_id=self.sosl.id,
                        booking_date=TODAY + timedelta(days=1), booking_time=time(11, 0), status='cancelled'),
                Booking(business_id=biz.id, client_id=client.id, service_id=service.id, employee_id=self.ilya.id,
                        booking_date=TODAY, booking_time=time(12, 0), status='confirmed'),
                Booking(business_id=biz.id, client_id=client.id, service_id=service.id, employee_id=None,
                        booking_date=TODAY, booking_time=time(14, 0), status='confirmed'),
                EmployeeDayOff(employee_id=self.sosl.id, date=TODAY + timedelta(days=3)),
                EmployeeDayOff(employee_id=self.ilya.id, date=TODAY + timedelta(days=3)),
            ])
            await db.commit()
            self.biz_id, self.owner_id = biz.id, owner.id
            self.sosl_id, self.ilya_id = self.sosl.id, self.ilya.id

        self.current_user_id = None
        app = FastAPI()
        app.include_router(web_master.router, prefix='/master')
        app.include_router(web_admin.router, prefix='/admin')

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

    async def _login_as(self, **user_fields):
        async with self.sessions() as db:
            user = User(full_name=user_fields.pop('full_name', 'Мастер'), **user_fields)
            db.add(user)
            await db.flush()
            await db.commit()
            self.current_user_id = user.id
            return user.id

    async def _add(self, *objects):
        async with self.sessions() as db:
            db.add_all(objects)
            await db.commit()

    # ---- who is a master ----

    async def test_telegram_login_matches_employee_by_telegram_id(self):
        await self._login_as(telegram_id=5551)
        body = self.client.get('/master/me').json()
        self.assertEqual([e['employee_name'] for e in body['employees']], ['Сослан'])
        self.assertEqual(body['employees'][0]['business_name'], 'Авто точка')

    async def test_email_login_matches_case_insensitively(self):
        user_id = await self._login_as(telegram_id=-777)
        await self._add(EmailIdentity(email='ilya@example.org', user_id=user_id, password_hash='x'))
        names = [e['employee_name'] for e in self.client.get('/master/me').json()['employees']]
        self.assertEqual(names, ['Илья'])

    async def test_yandex_verified_email_matches(self):
        user_id = await self._login_as(telegram_id=-778)
        await self._add(YandexIdentity(yandex_id='y1', user_id=user_id, email='sosl@example.org'))
        names = [e['employee_name'] for e in self.client.get('/master/me').json()['employees']]
        self.assertEqual(names, ['Сослан'])

    async def test_unverified_contact_email_does_not_grant_access(self):
        await self._login_as(telegram_id=-779, email='sosl@example.org')
        self.assertEqual(self.client.get('/master/me').json()['employees'], [])
        self.assertEqual(self.client.get('/master/schedule').status_code, 403)

    async def test_inactive_employee_has_no_cabinet(self):
        user_id = await self._login_as(telegram_id=-780)
        await self._add(EmailIdentity(email='gone@example.org', user_id=user_id, password_hash='x'))
        self.assertEqual(self.client.get('/master/me').json()['employees'], [])

    async def test_stranger_has_no_cabinet(self):
        await self._login_as(telegram_id=9999)
        self.assertEqual(self.client.get('/master/me').json()['employees'], [])
        self.assertEqual(self.client.get('/master/schedule').status_code, 403)

    # ---- what the master sees ----

    async def test_master_sees_only_own_bookings_without_prices(self):
        await self._login_as(telegram_id=5551)
        body = self.client.get('/master/schedule').json()
        self.assertEqual([(b['employee_name'], b['time'], b['status']) for b in body['bookings']],
                         [('Сослан', '10:00', 'confirmed'), ('Сослан', '11:00', 'cancelled')])
        first = body['bookings'][0]
        self.assertEqual(first['client_name'], 'Клиент Иван')
        self.assertEqual(first['client_phone'], '+79180000001')
        self.assertEqual(first['notes'], 'Скрипит перед')
        self.assertEqual(first['service_name'], 'Сход-развал')
        for forbidden in ('price', 'estimated_price', 'prepayment_amount', 'client_email', 'client_id'):
            self.assertNotIn(forbidden, first)

    async def test_days_off_are_only_the_masters_own(self):
        await self._login_as(telegram_id=5551)
        days_off = self.client.get('/master/schedule').json()['days_off']
        self.assertEqual(days_off, [{'employee_id': self.sosl_id, 'date': (TODAY + timedelta(days=3)).isoformat()}])

    async def test_period_limits(self):
        await self._login_as(telegram_id=5551)
        far = (TODAY + timedelta(days=200)).isoformat()
        self.assertEqual(self.client.get('/master/schedule', params={'date_to': far}).status_code, 422)
        self.assertEqual(self.client.get('/master/schedule', params={
            'date_from': TODAY.isoformat(), 'date_to': (TODAY - timedelta(days=1)).isoformat()}).status_code, 422)

    # ---- owner side ----

    async def test_owner_sets_access_email_normalised_and_invalid_is_rejected(self):
        self.current_user_id = self.owner_id
        url = f'/admin/businesses/{self.biz_id}/employees/{self.ilya_id}'
        res = self.client.put(url, json={'access_email': '  Master.New@Example.ORG '})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()['access_email'], 'master.new@example.org')
        self.assertEqual(self.client.put(url, json={'access_email': 'не email'}).status_code, 422)
        cleared = self.client.put(url, json={'access_email': ''})
        self.assertIsNone(cleared.json()['access_email'])

    async def test_owner_can_revoke_telegram_access(self):
        self.current_user_id = self.owner_id
        url = f'/admin/businesses/{self.biz_id}/employees/{self.sosl_id}'
        self.assertIsNone(self.client.put(url, json={'telegram_id': None}).json()['telegram_id'])
        await self._login_as(telegram_id=5551)
        names = [e['employee_name'] for e in self.client.get('/master/me').json()['employees']]
        self.assertEqual(names, [])

    async def test_public_employee_schema_never_exposes_access_email(self):
        self.assertNotIn('access_email', EmployeeOut.model_fields)


if __name__ == '__main__':
    unittest.main()
