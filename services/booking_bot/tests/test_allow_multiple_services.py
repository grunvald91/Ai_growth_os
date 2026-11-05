"""Business setting «несколько услуг в одной записи»: admin toggle + public exposure."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import unittest
from datetime import time
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from database.models import Base, User, Business, Service, Employee
from web.api.database import get_db
from web.api.routes import admin, client
from web.api.deps import get_current_admin


class AllowMultipleServicesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=11223344, full_name='Владелец')
            db.add(owner); await db.flush(); self.owner_id = owner.id
            business = Business(owner_id=owner.id, name='Студия', business_type='beauty', is_active=True,
                                working_hours_start=time(9), working_hours_end=time(21))
            db.add(business); await db.flush(); self.business_id = business.id
            cut = Service(business_id=business.id, name='Стрижка', duration_minutes=60, price=2000, is_active=True)
            style = Service(business_id=business.id, name='Укладка', duration_minutes=30, price=1000, is_active=True)
            master = Employee(business_id=business.id, name='Мастер', is_active=True)
            db.add_all([cut, style, master]); await db.flush()
            self.service_ids, self.master_id = [cut.id, style.id], master.id
            await db.commit()
        app = FastAPI()
        app.include_router(client.router)
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

    def test_defaults_to_single_service(self):
        res = self.client.get(f'/businesses/{self.business_id}')
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(res.json()['allow_multiple_services'])

    def test_owner_can_enable_and_null_is_ignored(self):
        res = self.client.put(f'/admin/businesses/{self.business_id}', json={'allow_multiple_services': True})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()['allow_multiple_services'])
        res = self.client.put(f'/admin/businesses/{self.business_id}', json={'allow_multiple_services': None})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()['allow_multiple_services'])
        self.assertTrue(self.client.get(f'/businesses/{self.business_id}').json()['allow_multiple_services'])

    def book_two(self):
        return self.client.post('/bookings', json={
            'business_id': self.business_id, 'service_ids': self.service_ids, 'employee_id': self.master_id,
            'date': '2030-01-07', 'time': '10:00', 'client_name': 'Клиент', 'client_phone': '+79990000000'})

    def test_server_rejects_several_services_when_disabled(self):
        res = self.book_two()
        self.assertEqual(res.status_code, 422, res.text)

    def test_server_accepts_several_services_when_enabled(self):
        self.client.put(f'/admin/businesses/{self.business_id}', json={'allow_multiple_services': True})
        res = self.book_two()
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(len(res.json()), 2)


if __name__ == '__main__':
    unittest.main()
