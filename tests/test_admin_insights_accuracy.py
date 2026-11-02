"""Regression: service add-ons are not extra visits and price estimates are not payments."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import date, time

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Booking, Business, Service, User
from web.api.database import get_db
from web.api.deps import get_current_admin
from web.api.routes.admin import router


class AdminInsightAccuracyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=8001, full_name="Владелец")
            visitor = User(telegram_id=8002, full_name="Клиент")
            db.add_all([owner, visitor])
            await db.flush()
            business = Business(owner_id=owner.id, name="Салон", business_type="salon", is_active=True)
            db.add(business)
            await db.flush()
            main = Service(business_id=business.id, name="Стрижка", duration_minutes=60, price=1000)
            addon = Service(business_id=business.id, name="Уход", duration_minutes=30, price=500)
            db.add_all([main, addon])
            await db.flush()
            db.add_all([
                Booking(business_id=business.id, client_id=visitor.id, service_id=main.id,
                        booking_date=date.today(), booking_time=time(12, 0), status="completed",
                        estimated_price=1200, resource_duration_minutes=60, occupies_resource=True),
                Booking(business_id=business.id, client_id=visitor.id, service_id=addon.id,
                        booking_date=date.today(), booking_time=time(12, 0), status="completed",
                        estimated_price=350, resource_duration_minutes=30, occupies_resource=False),
            ])
            await db.commit()
            self.owner_id, self.business_id = owner.id, business.id
        app = FastAPI()
        app.include_router(router, prefix="/admin")

        async def session():
            async with self.sessions() as db:
                yield db

        async def owner(db: AsyncSession = Depends(get_db)):
            return await db.get(User, self.owner_id)

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_admin] = owner
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await self.engine.dispose()

    async def test_addon_is_price_but_not_extra_visit_or_occupied_hour(self):
        dashboard = self.client.get("/admin/dashboard")
        self.assertEqual(dashboard.status_code, 200, dashboard.text)
        self.assertEqual(dashboard.json()["today_bookings"], 1)
        self.assertEqual(dashboard.json()["total_revenue"], 1550)  # legacy key: estimated booking value

        response = self.client.get(f"/admin/analytics/{self.business_id}/overview", params={"days": 7})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["kpis"]["bookings"], 1)
        self.assertEqual(data["kpis"]["revenue"], 1550)
        self.assertEqual(data["kpis"]["avg_check"], 1550)
        self.assertEqual(sum(day["bookings"] for day in data["daily"]), 1)
        self.assertEqual(sum(item["count"] for item in data["heatmap"]), 1)
