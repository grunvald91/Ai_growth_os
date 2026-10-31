"""SEO article -> real service -> signed source -> one booking visit -> owner counts.

Uses only in-memory SQLite, no real messages, paid AI, or network.
"""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from database.models import Base, Booking, Business, SeoBookingAttribution, Service, User
from web.api.schemas import CreateBookingIn
from web.api.seo_referrals import (
    TTL_SECONDS, article_booking_link, choose_service, make_reference, read_reference,
)
from web.api.routes.client import create_booking_visit
from web.api.routes.seo_reporting import seo_conversions


def svc(service_id, name, *, active=True, additional=False):
    return SimpleNamespace(id=service_id, name=name, is_active=active, is_additional=additional)


class ServiceAndTokenTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = patch.dict(os.environ, {"SEO_ATTRIBUTION_SECRET": "s" * 64})
        self.secret.start()

    def tearDown(self):
        self.secret.stop()

    def test_select_only_real_active_service_and_only_when_unambiguous(self):
        article = {"title": "Как выбрать окрашивание волос", "primary_query": "окрашивания волос"}
        self.assertEqual(choose_service(article, [
            svc(11, "Окрашивание волос"),
            svc(12, "Маникюр"),
            svc(13, "Окрашивание волос", active=False),
            svc(14, "Окрашивание волос", additional=True),
        ]), 11)
        self.assertIsNone(choose_service(article, [svc(12, "Маникюр")]))
        self.assertIsNone(choose_service(article, [svc(11, "Окрашивание волос"),
                                                  svc(15, "Окрашивание волос")]))
        self.assertIsNone(choose_service({"title": "Домашний уход за волосами"},
                                         [svc(16, "Услуга, которой нет в статье")]))

    def test_signing_ties_slug_business_and_expiry(self):
        token = make_reference(64, "okrashivanie-volos", now=10000)
        self.assertIsNotNone(token)
        self.assertEqual(read_reference(token, 64, now=10000), "okrashivanie-volos")
        self.assertIsNone(read_reference(token, 65, now=10000))
        self.assertIsNone(read_reference(token, 64, now=10000 + TTL_SECONDS + 1))
        self.assertIsNone(read_reference(token + "x", 64, now=10000))
        self.assertIsNone(read_reference("###.###", 64, now=10000))
        self.assertIsNone(read_reference("", 64, now=10000))
        with patch.dict(os.environ, {"SEO_ATTRIBUTION_SECRET": ""}):
            self.assertIsNone(read_reference(token, 64, now=10000))
            self.assertIsNone(make_reference(64, "okrashivanie-volos", now=10000))

    async def test_article_links_use_only_verified_catalog_ids(self):
        class DB:
            async def scalars(self, _query):
                return SimpleNamespace(all=lambda: [svc(11, "Окрашивание волос"),
                                                     svc(12, "Маникюр")])
        article = {"slug": "okrashivanie-volos", "title": "Как выбрать окрашивание волос",
                   "primary_query": "окрашивания волос"}
        link = await article_booking_link(DB(), 64, article)
        self.assertEqual(link["service_id"], 11)
        self.assertIn("/book/64?service=11&seo_ref=", link["cta_url"])
        self.assertEqual(read_reference(link["seo_ref"], 64), article["slug"])
        self.assertIn("/business/64?seo_ref=", link["company_url"])


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.secret = patch.dict(os.environ, {"SEO_ATTRIBUTION_SECRET": "s" * 64})
        self.secret.start()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=20001, full_name="Владелец")
            db.add(owner)
            await db.flush()
            business = Business(owner_id=owner.id, name="Салон", business_type="salon",
                                is_active=True, allow_multiple_services=True)
            db.add(business)
            await db.flush()
            db.add_all([
                Service(business_id=business.id, name="Окрашивание волос",
                        duration_minutes=60, price=1500, is_active=True),
                Service(business_id=business.id, name="Уход для волос",
                        duration_minutes=30, price=500, is_active=True),
            ])
            await db.commit()
            self.owner_id = owner.id
            self.business_id = business.id
            self.service_ids = (await db.scalars(select(Service.id).order_by(Service.id))).all()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.secret.stop()

    async def test_booking_one_referral_for_multiservice_and_verified_owner_summary(self):
        async with self.sessions() as db:
            owner = await db.get(User, self.owner_id)
            body = CreateBookingIn(
                business_id=self.business_id, service_ids=self.service_ids,
                date=date.today() + timedelta(days=2), time="10:00",
                client_name="Посетитель", client_phone="+79990000001",
                seo_ref=make_reference(self.business_id, "okrashivanie-volos"),
            )
            with patch("web.api.routes.client.lock_resource_days", new_callable=AsyncMock), \
                 patch("web.api.routes.client.ensure_resource_available", new_callable=AsyncMock):
                bookings = await create_booking_visit(body, db, owner, commit=False)
            await db.commit()
            self.assertEqual(len(bookings), 2)
            referred = (await db.scalars(select(SeoBookingAttribution))).all()
            self.assertEqual(len(referred), 1)
            self.assertEqual(referred[0].booking_id, bookings[0].id)
            self.assertEqual(referred[0].article_slug, "okrashivanie-volos")
            summary = await seo_conversions(self.business_id, 90, owner, db)
            self.assertEqual((summary["total_created"], summary["total_confirmed"]), (1, 0))
            booking = await db.get(Booking, referred[0].booking_id)
            booking.status = "confirmed"
            await db.commit()
            summary = await seo_conversions(self.business_id, 90, owner, db)
            self.assertEqual((summary["total_created"], summary["total_confirmed"]), (1, 1))

    async def test_invalid_or_cross_business_ref_does_not_create_attribution(self):
        async with self.sessions() as db:
            owner = await db.get(User, self.owner_id)
            body = CreateBookingIn(
                business_id=self.business_id, service_ids=[self.service_ids[0]],
                date=date.today() + timedelta(days=4), time="11:00",
                seo_ref=make_reference(self.business_id + 1, "fake-article"),
            )
            with patch("web.api.routes.client.lock_resource_days", new_callable=AsyncMock), \
                 patch("web.api.routes.client.ensure_resource_available", new_callable=AsyncMock):
                bookings = await create_booking_visit(body, db, owner, commit=False)
            await db.commit()
            self.assertEqual(len(bookings), 1)
            self.assertEqual((await db.scalars(select(SeoBookingAttribution))).all(), [])

    async def test_unavailable_attribution_table_never_breaks_real_booking(self):
        # Simulate a deployment where only the SEO statistics migration is missing.
        async with self.engine.begin() as conn:
            await conn.run_sync(SeoBookingAttribution.__table__.drop)
        async with self.sessions() as db:
            owner = await db.get(User, self.owner_id)
            body = CreateBookingIn(
                business_id=self.business_id, service_ids=[self.service_ids[0]],
                date=date.today() + timedelta(days=6), time="12:00",
                seo_ref=make_reference(self.business_id, "real-article"),
            )
            with patch("web.api.routes.client.lock_resource_days", new_callable=AsyncMock), \\
                 patch("web.api.routes.client.ensure_resource_available", new_callable=AsyncMock):
                created = await create_booking_visit(body, db, owner, commit=False)
            await db.commit()
            self.assertEqual(len(created), 1)
            self.assertIsNotNone(await db.get(Booking, created[0].id))

    async def test_other_owner_cannot_read_company_analytics(self):
        from fastapi import HTTPException
        async with self.sessions() as db:
            other = User(telegram_id=20002, full_name="Чужой владелец")
            db.add(other)
            await db.commit()
            with self.assertRaises(HTTPException) as error:
                await seo_conversions(self.business_id, 90, other, db)
            self.assertEqual(error.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
