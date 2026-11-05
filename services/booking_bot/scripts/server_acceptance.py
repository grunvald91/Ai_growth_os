"""Exercise the installed API using hidden, synthetic fixtures only.

Run from /opt/booking_bot with its venv, after loading the API service environment.
Creates two inactive businesses and unique test users; cleans up only those rows.
Does not transfer money, email customers, or change existing accounts.
Email verification is exercised with a seeded test challenge; SMTP delivery is a
separate acceptance check. The output never includes tokens, passwords, or codes.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import uuid
from datetime import date, time, timedelta

import httpx
from sqlalchemy import delete, func, select

from database.models import (
    Booking, BookingReceipt, Business, EmailChallenge, EmailIdentity, Employee,
    PrepaymentReview, Service, User,
)
from datetime_utils import utc_now
from web.api.database import async_session
from web.api.routes.email_auth import digest, hash_password


async def main():
    marker = "servgo_acceptance_" + uuid.uuid4().hex
    password = secrets.token_urlsafe(28)
    user_ids, business_ids = [], []
    results = []
    challenge_ids = []
    emails = [f"{marker}.{kind}@example.test" for kind in ("owner", "client", "outsider", "register")]
    encoded = await asyncio.to_thread(hash_password, password)
    day = date.today() + timedelta(days=3)
    report = {"fixture": marker, "checks": results, "real_money_transferred": False,
              "email_delivery_in_this_test": False}

    def record(name, passed):
        results.append({"check": name, "passed": bool(passed)})
        if not passed:
            raise AssertionError(name)

    try:
        async with async_session() as db:
            users = [User(telegram_id=-secrets.randbelow(2**60)-1,
                          full_name=marker + ":" + kind)
                     for kind in ("owner", "client", "outsider")]
            db.add_all(users)
            await db.flush()
            user_ids.extend(u.id for u in users)
            for u, email in zip(users, emails):
                db.add(EmailIdentity(email=email, user_id=u.id, password_hash=encoded))
            businesses = [Business(owner_id=users[i].id, name=marker + ":" + kind,
                                  business_type="salon", is_active=False,
                                  working_hours_start=time(9), working_hours_end=time(20),
                                  prepayment_enabled=True, prepayment_percent=50,
                                  require_receipt_for_prepayment=True,
                                  owner_reminders_enabled=False, master_reminders_enabled=False)
                          for i, kind in ((0, "target"), (2, "other"))]
            db.add_all(businesses)
            await db.flush()
            business_ids.extend(b.id for b in businesses)
            service = Service(business_id=business_ids[0], name=marker + ":service",
                              duration_minutes=60, price=2000, is_active=True)
            employee = Employee(business_id=business_ids[0], name=marker + ":resource",
                                role="master", is_active=True)
            db.add_all([service, employee])
            await db.flush()
            sid, eid = service.id, employee.id
            challenge_id, code = str(uuid.uuid4()), f"{secrets.randbelow(1000000):06d}"
            challenge_ids.append(challenge_id)
            db.add(EmailChallenge(id=challenge_id, email=emails[3], purpose="register",
                                 full_name=marker + ":register", password_hash=encoded,
                                 code_hash=digest(challenge_id + ":" + code),
                                 expires_at=utc_now() + timedelta(minutes=15)))
            await db.commit()

        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30) as api:
            auth = []
            for kind, email in zip(("owner", "client", "outsider"), emails):
                r = await api.post("/auth/email/login", json={"email": email, "password": password})
                record("email_login_" + kind, r.status_code == 200)
                data = r.json()
                record("email_role_" + kind, data["role"] == ("client" if kind == "client" else "admin"))
                auth.append({"Authorization": "Bearer " + data["token"]})
            owner, client, outsider = auth
            r = await api.post("/auth/email/verify", json={"challenge_id": challenge_id, "code": code})
            record("email_verification_creates_separate_client", r.status_code == 200 and r.json()["role"] == "client")
            user_ids.append(r.json()["user"]["id"])
            r = await api.post("/auth/email/verify", json={"challenge_id": challenge_id, "code": code})
            record("verification_code_is_single_use", r.status_code == 400)

            body = {"business_id": business_ids[0], "service_id": sid, "employee_id": eid,
                    "date": str(day), "time": "12:00", "notes": marker}
            r = await api.post("/bookings", headers=client, json=body)
            record("create_real_api_booking", r.status_code == 200)
            booking = r.json()[0]
            bid, token = booking["id"], booking["notify_token"]
            record("booking_waits_for_prepayment", booking["status"] == "pending_prepayment")
            r = await api.post("/bookings", headers=client, json=body)
            record("occupied_slot_rejects_duplicate", r.status_code == 409)
            r = await api.get("/my/bookings", headers=client)
            record("client_can_see_booking", r.status_code == 200 and any(x["id"] == bid for x in r.json()))
            public_path = "/bookings/by-token/" + token
            r = await api.get(public_path)
            record("tracking_page_api", r.status_code == 200 and r.json()["prepayment_amount"] == 1000)
            r = await api.post(public_path + "/prepayment-sent")
            record("receipt_required_before_payment_sent", r.status_code == 422)
            # Valid PNG signature: receipt admission uses signature/size validation.
            png = bytes.fromhex("89504e470d0a1a0a") + b"synthetic-acceptance-proof"
            r = await api.post(public_path + "/receipt", files={"file": ("test.png", png, "image/png")})
            record("upload_receipt_to_real_api", r.status_code == 200)
            r = await api.get("/admin/prepayments/attention", headers=owner)
            record("owner_attention_queue", r.status_code == 200 and r.json()["count"] == 1)
            details_path = f"/admin/bookings/{bid}/payment-details"
            r = await api.get(details_path, headers=owner)
            record("owner_can_review_receipt", r.status_code == 200 and len(r.json()["receipts"]) == 1)
            details = r.json()
            rid = details["receipts"][0]["id"]
            receipt_path = f"/admin/receipts/{rid}"
            r = await api.get(receipt_path, headers=owner)
            record("private_receipt_download", r.status_code == 200 and r.content == png
                   and r.headers.get("x-content-type-options") == "nosniff")
            r = await api.get(receipt_path, headers=outsider)
            record("other_owner_cannot_read_receipt", r.status_code == 404)
            r = await api.get(receipt_path)
            record("anonymous_cannot_read_receipt", r.status_code == 401)
            review_path = f"/admin/bookings/{bid}/prepayment-review"
            version = details["booking"]["version"]
            r = await api.post(review_path, headers=owner,
                               json={"decision": "reject", "expected_version": version})
            record("reject_returns_to_payment_wait", r.status_code == 200 and r.json()["status"] == "pending_prepayment")
            r = await api.post(public_path + "/receipt", files={"file": ("test.png", png, "image/png")})
            record("retry_receipt_after_rejection", r.status_code == 200)
            r = await api.get(details_path, headers=owner)
            version = r.json()["booking"]["version"]
            r = await api.post(review_path, headers=owner,
                               json={"decision": "confirm", "expected_version": version})
            record("owner_confirms_test_prepayment", r.status_code == 200 and r.json()["status"] == "confirmed")
            r = await api.post(review_path, headers=owner,
                               json={"decision": "reject", "expected_version": version})
            record("stale_owner_decision_rejected", r.status_code == 409)
            r = await api.get("/admin/prepayments/attention", headers=owner)
            record("attention_clears_after_decision", r.status_code == 200 and r.json()["count"] == 0)
            r = await api.post(f"/bookings/{bid}/cancel", headers=client)
            record("cancel_test_booking", r.status_code == 200 and r.json()["status"] == "cancelled")

            reset_id, reset_code = str(uuid.uuid4()), f"{secrets.randbelow(1000000):06d}"
            challenge_ids.append(reset_id)
            new_password = secrets.token_urlsafe(28)
            async with async_session() as db:
                db.add(EmailChallenge(id=reset_id, email=emails[1], purpose="reset", user_id=user_ids[1],
                                      password_hash=await asyncio.to_thread(hash_password, new_password),
                                      code_hash=digest(reset_id + ":" + reset_code),
                                      expires_at=utc_now() + timedelta(minutes=15)))
                await db.commit()
            r = await api.post("/auth/email/verify", json={"challenge_id": reset_id, "code": reset_code})
            record("reset_test_account_password", r.status_code == 200)
            r = await api.get("/auth/me", headers=client)
            record("password_reset_revokes_old_session", r.status_code == 401)
    finally:
        async with async_session() as db:
            # Guard against accidentally addressing a pre-existing business/user.
            owned = list((await db.scalars(select(Business).where(Business.id.in_(business_ids)))).all())
            assert all(x.name.startswith(marker + ":") for x in owned)
            guarded_users = list((await db.scalars(select(User).where(User.id.in_(user_ids)))).all())
            assert all(x.full_name.startswith(marker + ":") for x in guarded_users)
            bookings = select(Booking.id).where(Booking.business_id.in_(business_ids))
            await db.execute(delete(PrepaymentReview).where(PrepaymentReview.booking_id.in_(bookings)))
            await db.execute(delete(BookingReceipt).where(BookingReceipt.booking_id.in_(bookings)))
            await db.execute(delete(Booking).where(Booking.business_id.in_(business_ids)))
            await db.execute(delete(Employee).where(Employee.business_id.in_(business_ids)))
            await db.execute(delete(Service).where(Service.business_id.in_(business_ids)))
            await db.execute(delete(Business).where(Business.id.in_(business_ids)))
            await db.execute(delete(EmailChallenge).where(EmailChallenge.email.in_(emails)))
            await db.execute(delete(EmailIdentity).where(EmailIdentity.email.in_(emails)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
            # Registration can succeed just before a client/network failure.
            await db.execute(delete(User).where(User.full_name == marker + ":register"))
            await db.commit()
            remaining = await db.scalar(select(func.count()).select_from(User).where(User.full_name.startswith(marker + ":")))
            record("synthetic_accounts_cleaned", remaining == 0)
        print("ACCEPTANCE_REPORT " + json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        # Response bodies can contain secrets; never include them in diagnostics.
        print("ACCEPTANCE_FAILED " + type(exc).__name__ + (": " + str(exc) if isinstance(exc, AssertionError) else ""))
        sys.exit(1)
