"""End-to-end rental API tests using an in-memory SQLAlchemy database."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import date, timedelta, time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, User, Business, Employee, Service, Booking
from web.api.database import get_db
from web.api import rental
from web.api.routes import admin
from web.api.deps import get_current_admin


class RentalApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.day = date.today() + timedelta(days=3)
        async with self.sessions() as db:
            owner = User(telegram_id=88776655, full_name='Демо-владелец', username='Grunvald')
            db.add(owner);await db.flush();self.owner_id=owner.id
            business = Business(owner_id=owner.id, name='100boats ДЕМО', business_type='venue_rental',
                                is_active=True, booking_interval_minutes=60,
                                working_hours_start=time(9), working_hours_end=time(21))
            db.add(business);await db.flush()
            self.business_id = business.id
            vessel = Employee(business_id=business.id, name='Princess 45',
                              role='master', is_active=True, solo_price_per_hour=19000)
            other = Employee(business_id=business.id, name='Azimut 39',
                             role='master', is_active=True, solo_price_per_hour=16000)
            main = Service(business_id=business.id, name='Аренда яхты / катера',
                           duration_minutes=60, price=None, is_active=True, is_additional=False)
            photo = Service(business_id=business.id, name='Фото',
                            duration_minutes=0, price=15000, is_active=True, is_additional=True)
            transport = Service(business_id=business.id, name='Трансфер',
                                duration_minutes=0, price=5000, is_active=True, is_additional=True)
            db.add_all([vessel,other,main,photo,transport]);await db.commit()
            self.vessel=vessel.id;self.other=other.id;self.photo=photo.id;self.transport=transport.id
        app=FastAPI();app.include_router(rental.router);app.include_router(admin.router,prefix='/admin')
        async def owner_user():
            async with self.sessions() as db:return await db.get(User,self.owner_id)
        app.dependency_overrides[get_current_admin]=owner_user
        async def session():
            async with self.sessions() as db:yield db
        app.dependency_overrides[get_db]=session
        self.client=TestClient(app)

    async def asyncTearDown(self):
        self.client.close();await self.engine.dispose()

    def payload(self, **changes):
        data={'date':self.day.isoformat(),'employee_id':self.vessel,'duration_hours':5,
              'time':'11:00','client_name':'Тест','client_phone':'+79990000001',
              'additional_service_ids':[self.photo,self.transport]}
        data.update(changes);return data

    async def test_five_hour_booking_is_persisted_and_blocks_overlap_but_not_other_vessel(self):
        endpoint=f'/businesses/{self.business_id}/rental/bookings'
        response=self.client.post(endpoint,json=self.payload())
        self.assertEqual(response.status_code,201,response.text)
        data=response.json();self.assertEqual(data['estimated_price'],115000)
        self.assertFalse(data['price_requires_confirmation'])
        self.assertEqual(data['duration_minutes'],300)
        self.assertTrue(data['tracking_token'])
        self.assertIsNone(data['prepayment_amount'])
        premature=self.client.patch(f"/admin/bookings/{data['id']}/status",json={'status':'confirmed'})
        self.assertEqual(premature.status_code,409,premature.text)
        zero_deposit=self.client.patch(f"/admin/rental/bookings/{data['id']}/quote",
                                       json={'estimated_price':115000,'prepayment_amount':0})
        self.assertEqual(zero_deposit.status_code,200,zero_deposit.text)
        self.assertEqual(zero_deposit.json()['prepayment_amount'],0)
        confirmed=self.client.patch(f"/admin/bookings/{data['id']}/status",json={'status':'confirmed'})
        self.assertEqual(confirmed.status_code,200,confirmed.text)
        async with self.sessions() as db:
            booking=await db.get(Booking,data['id'])
            self.assertEqual((booking.employee_id,booking.duration_minutes,booking.estimated_price),
                             (self.vessel,300,115000))
        collision=self.client.post(endpoint,json=self.payload(time='14:00'))
        self.assertEqual(collision.status_code,409,collision.text)
        other=self.client.post(endpoint,json=self.payload(employee_id=self.other,additional_service_ids=[]))
        self.assertEqual(other.status_code,201,other.text)
        slots=self.client.get(f'/businesses/{self.business_id}/rental/slots',params={
            'date':self.day.isoformat(),'employee_id':self.vessel,'duration_hours':5})
        self.assertEqual(slots.status_code,200,slots.text)
        self.assertNotIn('10:00',slots.json()['slots'])
        self.assertIn('16:00',slots.json()['slots'])

    async def test_short_booking_needs_manual_quote_and_status_is_readable_by_token(self):
        response=self.client.post(f'/businesses/{self.business_id}/rental/bookings',
                                  json=self.payload(duration_hours=3,additional_service_ids=[]))
        self.assertEqual(response.status_code,201,response.text)
        booking=response.json()
        self.assertIsNone(booking['estimated_price'])
        self.assertTrue(booking['price_requires_confirmation'])
        status=self.client.get(f"/rental/bookings/{booking['tracking_token']}")
        self.assertEqual(status.status_code,200,status.text)
        self.assertEqual(status.json()['status'],'pending')
        self.assertEqual(self.client.get('/rental/bookings/not-a-valid-token').status_code,404)

    async def test_owner_confirms_short_price_and_deposit_then_booking(self):
        created=self.client.post(f'/businesses/{self.business_id}/rental/bookings',
                                 json=self.payload(duration_hours=3,additional_service_ids=[])).json()
        bid=created['id']
        premature=self.client.patch(f'/admin/bookings/{bid}/status',json={'status':'confirmed'})
        self.assertEqual(premature.status_code,409,premature.text)
        excessive=self.client.patch(f'/admin/rental/bookings/{bid}/quote',
                                    json={'estimated_price':10000,'prepayment_amount':2001})
        self.assertEqual(excessive.status_code,400,excessive.text)
        quote=self.client.patch(f'/admin/rental/bookings/{bid}/quote',
                                json={'estimated_price':10000,'prepayment_amount':2000})
        self.assertEqual(quote.status_code,200,quote.text)
        self.assertFalse(quote.json()['price_requires_confirmation'])
        confirmed=self.client.patch(f'/admin/bookings/{bid}/status',json={'status':'confirmed'})
        self.assertEqual(confirmed.status_code,200,confirmed.text)
        self.assertEqual(confirmed.json()['status'],'confirmed')

    async def test_rejects_cross_business_vessel_and_addon(self):
        bad=self.client.post(f'/businesses/{self.business_id}/rental/bookings',
                             json=self.payload(employee_id=999))
        self.assertEqual(bad.status_code,404)
        bad=self.client.post(f'/businesses/{self.business_id}/rental/bookings',
                             json=self.payload(additional_service_ids=[999]))
        self.assertEqual(bad.status_code,400)


if __name__=='__main__':unittest.main()
