"""Studio service-aware slot contract: exact duration, business isolation, bounded work."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import date, timedelta, time
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Business, Employee, Service, User
from web.api.database import get_db
from web.api.routes.studio_schedule import router

SECRET = "s" * 40


class StudioSlotOpportunitiesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=811, full_name="Владелец")
            db.add(owner)
            await db.flush()
            business = Business(owner_id=owner.id, name="Салон", business_type="salon", is_active=True)
            other = Business(owner_id=owner.id, name="Другая", business_type="salon", is_active=True)
            db.add_all([business, other])
            await db.flush()
            db.add_all([
                Service(business_id=business.id, name="Маникюр", duration_minutes=90, price=2500),
                Service(business_id=business.id, name="Дополнение", duration_minutes=15,
                        is_additional=True, price=500),
                Service(business_id=other.id, name="Чужая услуга", duration_minutes=60, price=900),
                Employee(business_id=business.id, name="Мастер", role="master", is_active=True),
                Employee(business_id=business.id, name="Админ", role="administrator", is_active=True),
            ])
            await db.commit()
            self.business_id = business.id
            self.service_id = (await db.scalars(
                __import__("sqlalchemy").select(Service.id).where(
                    Service.business_id == business.id, Service.is_additional.is_(False))
            )).first()
            self.other_service_id = (await db.scalars(
                __import__("sqlalchemy").select(Service.id).where(Service.business_id == other.id)
            )).first()
        app = FastAPI()
        app.include_router(router)
        async def session():
            async with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = session
        self.client = TestClient(app)
        self.secret_patch = patch("web.api.routes.studio_schedule._secret", return_value=SECRET)
        self.secret_patch.start()

    async def asyncTearDown(self):
        self.secret_patch.stop()
        self.client.close()
        await self.engine.dispose()

    def url(self, **extra):
        today = date.today() + timedelta(days=1)
        params = {"business_id": self.business_id, "date_from": today.isoformat(),
                  "date_to": today.isoformat(), **extra}
        return params

    async def test_service_filter_is_passed_to_existing_availability_engine(self):
        from web.api.schemas import SlotOut
        with patch("web.api.routes.client.get_slots",
                   new=AsyncMock(return_value=[SlotOut(time="11:30", available=True)])) as mock_slots:
            result = self.client.get("/internal/studio/schedule",
                params=self.url(with_slots=True, slot_service_id=self.service_id),
                headers={"X-Studio-Key": SECRET})
            self.assertEqual(result.status_code, 200, result.text)
            payload = result.json()
            self.assertEqual(len(payload["resources"]), 1)  # administrator excluded
            self.assertEqual(len(payload["services"]), 1)  # add-on excluded
            self.assertEqual(payload["free_slots"][0]["service_id"], self.service_id)
            self.assertEqual(payload["free_slots"][0]["times"], ["11:30"])
            self.assertEqual(mock_slots.call_args.kwargs["service_id"], self.service_id)
            self.assertIsNone(mock_slots.call_args.kwargs["duration_minutes"])  # service determines duration

    async def test_cross_business_service_and_invalid_window_fail_before_slot_query(self):
        for opts, code in [
            ({"with_slots": True, "slot_service_id": self.other_service_id}, 404),
            ({"with_slots": True, "slot_service_id": self.service_id, "slot_minutes": 5}, 422),
        ]:
            result = self.client.get("/internal/studio/schedule", params=self.url(**opts),
                                     headers={"X-Studio-Key": SECRET})
            self.assertEqual(result.status_code, code, result.text)
        start = date.today() + timedelta(days=1)
        params = self.url(with_slots=True, slot_service_id=self.service_id)
        params["date_to"] = (start + timedelta(days=7)).isoformat()
        result = self.client.get("/internal/studio/schedule", params=params,
                                 headers={"X-Studio-Key": SECRET})
        self.assertEqual(result.status_code, 422)
        self.assertEqual(self.client.get("/internal/studio/schedule", params=self.url(),
                                         headers={"X-Studio-Key": "invalid"}).status_code, 403)

    async def test_incompatible_master_does_not_fail_entire_read(self):
        with patch("web.api.routes.client.get_slots",
                   new=AsyncMock(side_effect=HTTPException(status_code=422, detail="Not offered"))):
            response = self.client.get("/internal/studio/schedule",
                params=self.url(with_slots=True, slot_service_id=self.service_id),
                headers={"X-Studio-Key": SECRET})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["free_slots"], [])
