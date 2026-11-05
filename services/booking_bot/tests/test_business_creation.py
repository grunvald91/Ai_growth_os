"""Создание бизнеса: выбор типа в боте (состояние диалога = JSON) и создание из веб-админки."""
import os
os.environ.setdefault('BOT_TOKEN', 'test-token')
os.environ.setdefault('DATABASE_URL', 'sqlite+aiosqlite:///:memory:')

import json
import unittest
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import StaticPool

from bot.handlers import admin as bot_admin
from bot.states import BusinessCreationStates
from database.models import Base, User, Business, BusinessOwner, Service
from web.api.database import get_db
from web.api.deps import get_current_admin
from web.api.routes import admin as web_admin


class JsonOnlyStorage(MemoryStorage):
    """Как RedisStorage: данные диалога должны сериализоваться в JSON, иначе запись падает."""

    async def update_data(self, key, data):
        json.dumps(data)
        return await super().update_data(key, data)


class BotBusinessTypeChoiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_choosing_type_stores_json_safe_state_and_moves_on(self):
        storage = JsonOnlyStorage()
        state = FSMContext(storage=storage, key=StorageKey(bot_id=1, chat_id=1, user_id=1))
        await state.set_state(BusinessCreationStates.entering_type)

        callback = MagicMock()
        callback.data = 'business_type:tire_service'
        callback.message.edit_text = AsyncMock()
        callback.answer = AsyncMock()

        await bot_admin.business_type_chosen(callback, state)

        self.assertEqual(await state.get_state(), BusinessCreationStates.entering_description.state)
        data = await state.get_data()
        self.assertEqual(data['working_hours_start'], '08:00')
        self.assertEqual(data['break_start'], '12:00')
        callback.message.edit_text.assert_awaited_once()

    def test_time_helpers_round_trip(self):
        self.assertEqual(bot_admin._time_to_state(time(9, 30)), '09:30')
        self.assertIsNone(bot_admin._time_to_state(None))
        self.assertEqual(bot_admin._time_from_state('09:30'), time(9, 30))
        self.assertEqual(bot_admin._time_from_state(time(9, 30)), time(9, 30))
        self.assertIsNone(bot_admin._time_from_state(None))


class WebCreateBusinessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:', poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=555000111, full_name='Владелец')
            db.add(owner)
            await db.flush()
            self.owner_id = owner.id
            await db.commit()

        app = FastAPI()
        app.include_router(web_admin.router, prefix='/admin')

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
        self.client.close()
        await self.engine.dispose()

    def _allow(self, allowed=True, reason=''):
        return patch('database.crud.can_user_create_business', AsyncMock(return_value=(allowed, reason)))

    async def test_creates_business_from_template_and_makes_user_owner(self):
        with self._allow():
            res = self.client.post('/admin/businesses', json={
                'name': '  Шиномонтаж на Мира ', 'business_type': 'tire_service',
                'address': 'ул. Мира, 1', 'phone': '8 (918) 000-11-22',
            })
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body['name'], 'Шиномонтаж на Мира')
        self.assertEqual(body['business_type'], 'tire_service')
        self.assertEqual(body['working_hours_start'], '08:00:00')
        self.assertEqual(body['break_start'], '12:00:00')
        self.assertEqual(body['booking_interval_minutes'], 30)
        self.assertIn('Шиномонтаж', body['description'])

        async with self.sessions() as db:
            business = await db.get(Business, body['id'])
            self.assertEqual(business.owner_id, self.owner_id)
            link = await db.scalar(select(BusinessOwner).where(
                BusinessOwner.business_id == body['id'], BusinessOwner.user_id == self.owner_id))
            self.assertIsNotNone(link)

    async def test_venue_rental_gets_default_rental_services(self):
        with self._allow():
            res = self.client.post('/admin/businesses', json={'name': 'Сауна', 'business_type': 'venue_rental'})
        self.assertEqual(res.status_code, 200, res.text)
        async with self.sessions() as db:
            names = (await db.scalars(select(Service.name).where(Service.business_id == res.json()['id']))).all()
        self.assertEqual(sorted(names), ['1 час', '2 часа', '3 часа'])

    async def test_subscription_rule_is_enforced_with_its_message(self):
        with self._allow(False, 'Достигнут лимит бизнесов для тарифа «Стандарт» (1).'):
            res = self.client.post('/admin/businesses', json={'name': 'Ещё один', 'business_type': 'salon'})
        self.assertEqual(res.status_code, 403)
        self.assertIn('Достигнут лимит', res.json()['detail'])
        async with self.sessions() as db:
            self.assertEqual((await db.scalars(select(Business))).all(), [])

    async def test_rejects_unknown_type_empty_name_and_bad_phone(self):
        with self._allow():
            bad_type = self.client.post('/admin/businesses', json={'name': 'X', 'business_type': 'nope'})
            blank = self.client.post('/admin/businesses', json={'name': '   ', 'business_type': 'salon'})
            bad_phone = self.client.post('/admin/businesses', json={
                'name': 'X', 'business_type': 'salon', 'phone': '12345'})
        self.assertEqual(bad_type.status_code, 422)
        self.assertEqual(blank.status_code, 422)
        self.assertEqual(bad_phone.status_code, 422)


if __name__ == '__main__':
    unittest.main()
