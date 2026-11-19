"""Business AI pilot: source-grounded metrics and access control."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import date, time
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Booking, Business, Service, User
from web.api.business_ai import build_business_agent_brief
from web.api.database import get_db
from web.api.deps import get_current_admin
from web.api.routes.business_ai import router


class BusinessAiBriefUnitTests(unittest.TestCase):
    def test_previous_zero_does_not_invent_growth_percent(self):
        point = SimpleNamespace(date=date(2026, 11, 18))
        kpis = SimpleNamespace(
            revenue=1000, revenue_prev=0, bookings=1, bookings_prev=0,
            avg_check=1000, avg_check_prev=0, cancellation_rate=0.0,
            cancellation_rate_prev=0.0, load_percent=None, load_percent_prev=None,
        )
        brief = build_business_agent_brief(business_id=7, overview=SimpleNamespace(
            kpis=kpis, daily=[point], top_services=[], top_employees=[]))
        self.assertIsNone(brief["metrics"]["booked_value_estimate_rub"]["change_percent"])
        self.assertIsNone(brief["metrics"]["approximate_load_percent"]["delta"])
        self.assertEqual(brief["compare_to"], {"start": "2026-11-17", "end": "2026-11-17"})
        self.assertTrue(brief["read_only"])


class BusinessAiBriefApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=8801, full_name="Владелец")
            outsider = User(telegram_id=8802, full_name="Посторонний")
            client = User(telegram_id=8803, full_name="Клиент")
            db.add_all([owner, outsider, client])
            await db.flush()
            business = Business(owner_id=owner.id, name="Студия", business_type="salon", is_active=True)
            db.add(business)
            await db.flush()
            main = Service(business_id=business.id, name="Стрижка", duration_minutes=60, price=1000)
            addon = Service(business_id=business.id, name="Уход", duration_minutes=30, price=500)
            db.add_all([main, addon])
            await db.flush()
            db.add_all([
                Booking(business_id=business.id, client_id=client.id, service_id=main.id,
                        booking_date=date.today(), booking_time=time(12), status="completed",
                        estimated_price=1200, resource_duration_minutes=60, occupies_resource=True),
                Booking(business_id=business.id, client_id=client.id, service_id=addon.id,
                        booking_date=date.today(), booking_time=time(12), status="completed",
                        estimated_price=350, resource_duration_minutes=30, occupies_resource=False),
            ])
            await db.commit()
            self.owner_id, self.outsider_id, self.business_id = owner.id, outsider.id, business.id
        app = FastAPI()
        app.include_router(router, prefix="/admin")
        self.viewer_id = self.owner_id

        async def session():
            async with self.sessions() as db:
                yield db

        async def viewer(db: AsyncSession = Depends(get_db)):
            return await db.get(User, self.viewer_id)

        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_admin] = viewer
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await self.engine.dispose()

    async def test_verified_estimates_not_payments_or_extra_visits(self):
        response = self.client.get(f"/admin/analytics/{self.business_id}/agent-brief", params={"days": 7})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["read_only"])
        self.assertEqual(result["metrics"]["bookings"]["current"], 1)
        self.assertEqual(result["metrics"]["booked_value_estimate_rub"]["current"], 1550)
        self.assertEqual(sum(s["line_items"] for s in result["top_services"]), 2)
        self.assertTrue(all("bookings" not in s for s in result["top_services"]))
        self.assertEqual(result["data_source"], f"/admin/analytics/{self.business_id}/overview")
        self.assertIn("НЕ фактическая оплата", result["data_limits"][0])

    async def test_other_business_is_forbidden(self):
        self.viewer_id = self.outsider_id
        response = self.client.get(f"/admin/analytics/{self.business_id}/agent-brief")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_period_is_bounded(self):
        response = self.client.get(f"/admin/analytics/{self.business_id}/agent-brief", params={"days": 366})
        self.assertEqual(response.status_code, 422)
