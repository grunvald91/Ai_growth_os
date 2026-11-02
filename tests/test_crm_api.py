"""CRM API isolation and regression tests; no external database or GitHub Actions."""
import os
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import unittest
from datetime import date, time, timedelta

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy import select

from database.models import Base, Booking, Business, CRMContact, SalesConversation, Service, User
from database.account_link import AccountLinkError, move_account_data
from web.api.database import get_db
from web.api.deps import get_current_admin
from web.api.routes.crm import router


class CRMApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.sessions() as db:
            owner = User(telegram_id=901, full_name="Владелец")
            foreign_owner = User(telegram_id=902, full_name="Чужой владелец")
            booked = User(telegram_id=903, full_name="Клиент А", phone="+79990000001")
            chatted = User(telegram_id=904, full_name="Клиент Б", phone="+79990000002")
            foreign_client = User(telegram_id=905, full_name="Чужой клиент")
            db.add_all([owner, foreign_owner, booked, chatted, foreign_client])
            await db.flush()
            a = Business(owner_id=owner.id, name="А", business_type="salon", is_active=True)
            b = Business(owner_id=owner.id, name="Б", business_type="salon", is_active=True)
            foreign = Business(owner_id=foreign_owner.id, name="В", business_type="salon", is_active=True)
            db.add_all([a, b, foreign]); await db.flush()
            service = Service(business_id=a.id, name="Стрижка", duration_minutes=60, price=1000)
            service_b = Service(business_id=b.id, name="Маникюр", duration_minutes=60)
            other_service = Service(business_id=foreign.id, name="Чужая услуга", duration_minutes=60)
            db.add_all([service, service_b, other_service]); await db.flush()
            db.add_all([
                Booking(business_id=a.id, client_id=booked.id, service_id=service.id,
                        booking_date=date.today() + timedelta(days=2), booking_time=time(12, 0),
                        status="confirmed", resource_duration_minutes=60),
                Booking(business_id=b.id, client_id=booked.id, service_id=service_b.id,
                        booking_date=date.today() + timedelta(days=4), booking_time=time(11, 0),
                        status="confirmed", resource_duration_minutes=60),
                Booking(business_id=foreign.id, client_id=foreign_client.id,
                        service_id=other_service.id, booking_date=date.today(),
                        booking_time=time(14, 0), status="completed", resource_duration_minutes=60),
                SalesConversation(id="13c4d87e-b017-4d88-91f0-c66a64e04711", business_id=a.id,
                                  principal=f"user:{chatted.id}", messages=[], facts={}),
                SalesConversation(id="13c4d87e-b017-4d88-91f0-c66a64e04712", business_id=a.id,
                                  principal="guest:an-unverified-browser", messages=[], facts={}),
            ])
            await db.commit()
            self.owner_id = owner.id
            self.booked_id = booked.id
            self.chatted_id = chatted.id
            self.foreign_client_id = foreign_client.id
            self.a, self.b, self.foreign = a.id, b.id, foreign.id

        app = FastAPI()
        app.include_router(router, prefix="/admin")
        async def session():
            async with self.sessions() as db:
                yield db
        async def authenticated_owner(db: AsyncSession = Depends(get_db)):
            return await db.get(User, self.owner_id)
        app.dependency_overrides[get_db] = session
        app.dependency_overrides[get_current_admin] = authenticated_owner
        self.client = TestClient(app)

    async def asyncTearDown(self):
        self.client.close()
        await self.engine.dispose()

    def url(self, business_id, suffix=""):
        return f"/admin/businesses/{business_id}/contacts{suffix}"

    async def test_list_auto_includes_bookings_and_registered_chat_only(self):
        response = self.client.get(self.url(self.a))
        self.assertEqual(response.status_code, 200, response.text)
        records = response.json()["items"]
        self.assertEqual({r["key"] for r in records},
                         {f"u:{self.booked_id}", f"u:{self.chatted_id}"})
        booked = next(r for r in records if r["key"] == f"u:{self.booked_id}")
        self.assertEqual(booked["bookings_count"], 1)
        self.assertEqual(booked["phone"], "+79990000001")
        chat_only = next(r for r in records if r["key"] == f"u:{self.chatted_id}")
        self.assertIsNone(chat_only["phone"])
        self.assertIsNone(self.client.get(self.url(self.a, f"/u:{self.chatted_id}")).json()["phone"])
        other_business = self.client.get(self.url(self.b)).json()["items"]
        self.assertEqual([r["key"] for r in other_business], [f"u:{self.booked_id}"])
        self.assertEqual(other_business[0]["bookings_count"], 1)

    async def test_additional_service_does_not_create_extra_completed_visit(self):
        async with self.sessions() as db:
            primary = (await db.scalars(select(Booking).where(
                Booking.business_id == self.a, Booking.client_id == self.booked_id))).first()
            primary.status = "completed"
            extra = Service(business_id=self.a, name="Дополнительный уход",
                            duration_minutes=15, price=350)
            db.add(extra)
            await db.flush()
            db.add(Booking(business_id=self.a, client_id=self.booked_id,
                           service_id=extra.id, booking_date=primary.booking_date,
                           booking_time=primary.booking_time, status="completed",
                           occupies_resource=False, resource_duration_minutes=15))
            await db.commit()
        contacts = self.client.get(self.url(self.a)).json()["items"]
        contact = next(item for item in contacts if item["key"] == f"u:{self.booked_id}")
        self.assertEqual(contact["bookings_count"], 1)
        self.assertEqual(contact["completed_count"], 1)
        details = self.client.get(self.url(self.a, f"/u:{self.booked_id}")).json()
        self.assertEqual(details["bookings_count"], 1)
        self.assertEqual(details["completed_count"], 1)
        self.assertEqual(len(details["history"]), 2)  # Service history retains the add-on.

    async def test_private_notes_do_not_change_global_user_or_other_business(self):
        key = f"u:{self.booked_id}"
        response = self.client.patch(self.url(self.a, "/" + key),
                                     json={"notes": "Любит утренние слоты", "stage": "vip",
                                           "name": "Имя только для салона А"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stage"], "vip")
        self.assertEqual(response.json()["history"][0]["service"], "Стрижка")
        self.assertEqual(response.json()["next_booking"], (date.today() + timedelta(days=2)).isoformat())
        self.assertEqual(self.client.get(self.url(self.a, "/" + key)).json()["notes"], "Любит утренние слоты")
        async with self.sessions() as db:
            global_user = await db.get(User, self.booked_id)
            self.assertEqual(global_user.full_name, "Клиент А")
            row = await db.get(CRMContact, 1)
            self.assertEqual(row.business_id, self.a)
        in_b = self.client.get(self.url(self.b, "/" + key)).json()
        self.assertEqual(in_b["name"], "Клиент А")
        self.assertEqual(in_b["notes"], "")
        self.assertEqual([b["service"] for b in in_b["history"]], ["Маникюр"])

    async def test_owner_can_create_find_and_delete_manual_contact(self):
        created = self.client.post(self.url(self.a), json={
            "name": "Новый контакт", "phone": "+79991112233", "notes": "Перезвонить",
            "follow_up_on": date.today().isoformat(),
        })
        self.assertEqual(created.status_code, 201, created.text)
        key = created.json()["key"]
        self.assertTrue(key.startswith("m:"))
        due = self.client.get(self.url(self.a), params={"stage": "follow_up"}).json()
        self.assertEqual([r["key"] for r in due["items"]], [key])
        self.assertEqual(self.client.delete(self.url(self.a, "/" + key)).json(), {"ok": True})
        self.assertEqual(self.client.get(self.url(self.a, "/" + key)).status_code, 404)

    async def test_cross_tenant_contact_read_and_write_denied(self):
        self.assertEqual(self.client.get(self.url(self.foreign)).status_code, 403)
        self.assertEqual(self.client.post(self.url(self.foreign), json={"name": "No"}).status_code, 403)
        key = f"u:{self.foreign_client_id}"
        self.assertEqual(self.client.get(self.url(self.a, "/" + key)).status_code, 404)
        self.assertEqual(self.client.patch(self.url(self.a, "/" + key),
                                           json={"notes": "leak"}).status_code, 404)
        self.assertEqual(self.client.delete(self.url(self.a, f"/u:{self.booked_id}")).status_code, 409)

    async def test_account_merge_preserves_crm_in_each_business(self):
        old_key = f"/u:{self.booked_id}"
        for business, note in ((self.a, "Заметка А"), (self.b, "Заметка Б")):
            response = self.client.patch(self.url(business, old_key), json={
                "notes": note, "stage": "vip", "follow_up_on": date.today().isoformat(),
            })
            self.assertEqual(response.status_code, 200)
        async with self.sessions() as db:
            await move_account_data(db, await db.get(User, self.booked_id),
                                    await db.get(User, self.chatted_id))
            await db.commit()
        for business, note in ((self.a, "Заметка А"), (self.b, "Заметка Б")):
            response = self.client.get(self.url(business, f"/u:{self.chatted_id}"))
            self.assertEqual(response.status_code, 200)
            contact = response.json()
            self.assertEqual((contact["notes"], contact["stage"], contact["follow_up_on"]),
                             (note, "vip", date.today().isoformat()))
            self.assertEqual(contact["bookings_count"], 1)

    async def test_account_merge_combines_complementary_crm_cards(self):
        self.client.patch(self.url(self.a, f"/u:{self.booked_id}"), json={
            "notes": "Предпочтения", "phone": "+79991234567", "stage": "vip",
        })
        self.client.patch(self.url(self.a, f"/u:{self.chatted_id}"), json={
            "notes": "Договорённости", "email": "client@example.org",
        })
        async with self.sessions() as db:
            await move_account_data(db, await db.get(User, self.booked_id),
                                    await db.get(User, self.chatted_id))
            await db.commit()
        contact = self.client.get(self.url(self.a, f"/u:{self.chatted_id}")).json()
        self.assertEqual(contact["notes"], "Договорённости\n\nПредпочтения")
        self.assertEqual((contact["phone"], contact["email"], contact["stage"]),
                         ("+79991234567", "client@example.org", "vip"))
        async with self.sessions() as db:
            cards = (await db.scalars(select(CRMContact))).all()
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].user_id, self.chatted_id)

    async def test_account_merge_rejects_conflicting_crm_without_data_loss(self):
        for field, source, target in (
            ("phone", "+79990000001", "+79990000002"),
            ("stage", "vip", "paused"),
            ("follow_up_on", date.today(), date.today() + timedelta(days=1)),
            ("notes", "a" * 3000, "b" * 3000),
        ):
            with self.subTest(field=field):
                async with self.sessions() as db:
                    db.add_all([
                        CRMContact(business_id=self.a, user_id=self.booked_id, **{field: source}),
                        CRMContact(business_id=self.a, user_id=self.chatted_id, **{field: target}),
                    ])
                    await db.flush()
                    with self.assertRaises(AccountLinkError):
                        await move_account_data(db, await db.get(User, self.booked_id),
                                                await db.get(User, self.chatted_id))
                    cards = (await db.scalars(select(CRMContact).order_by(CRMContact.id))).all()
                    self.assertEqual([getattr(c, field) for c in cards], [source, target])
                    booking = await db.scalar(select(Booking).where(Booking.business_id == self.a))
                    self.assertEqual(booking.client_id, self.booked_id)
                    await db.rollback()


if __name__ == "__main__":
    unittest.main()
