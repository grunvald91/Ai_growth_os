#!/usr/bin/env python3
"""
One-time seed script: populate the demo businesses (name containing
"пример"/"example"/"demo", case-insensitive) with ~30 days of realistic
UPCOMING booking data (tomorrow through +30 days), so the schedule/calendar
view has something to show going forward, not just history.

Companion to scripts/seed_demo_analytics_data.py, which seeds the PAST
30 days. This script covers the FUTURE instead — statuses are limited to
what's plausible for a booking that hasn't happened yet (no "completed").

This creates PERMANENT data — it is NOT cleaned up afterward. Every
booking it creates is tagged in `notes` with DEMO_MARKER; before inserting
anything, it checks whether that marker already exists in the target date
range for the target businesses and aborts if so, to avoid creating
duplicates on an accidental re-run.

Run FROM /opt/booking_bot with its own venv:
    cd /opt/booking_bot && ./venv/bin/python3 scripts/seed_demo_future_bookings.py
"""
import asyncio
import random
import sys
from datetime import date, datetime, time, timedelta

sys.path.insert(0, "/opt/booking_bot")

from sqlalchemy import select, or_
from database import async_session_maker
from database.models import Business, Employee, Service
from database.models import Booking as BookingModel
from database.crud import create_booking, get_or_create_guest_user

DEMO_MARKER = "[DEMO-SEED-FUTURE-2026-07]"
DAYS_FORWARD = 30

FIRST_NAMES = [
    "Александр", "Дмитрий", "Максим", "Сергей", "Андрей", "Алексей", "Артём",
    "Иван", "Михаил", "Никита", "Матвей", "Роман", "Егор", "Кирилл", "Владимир",
    "Елена", "Анна", "Мария", "Ольга", "Наталья", "Татьяна", "Ирина", "Светлана",
    "Юлия", "Екатерина", "Дарья", "Виктория", "Полина", "Алина", "Ксения",
]
LAST_NAMES = [
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Соколов", "Лебедев", "Козлов",
    "Новиков", "Морозов", "Волков", "Алексеев", "Егоров", "Павлов", "Семёнов",
    "Голубев", "Виноградов", "Богданов", "Воробьёв", "Фёдоров", "Михайлов",
]


def random_client_name() -> str:
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"


def random_phone() -> str:
    return f"+79{random.randint(100000000, 999999999)}"


async def find_demo_businesses(session):
    result = await session.execute(
        select(Business).where(
            or_(
                Business.name.ilike("%пример%"),
                Business.name.ilike("%example%"),
                Business.name.ilike("%demo%"),
            )
        )
    )
    return result.scalars().all()


async def already_seeded(session, business_ids, start_date, end_date):
    result = await session.execute(
        select(BookingModel.id).where(
            BookingModel.business_id.in_(business_ids),
            BookingModel.booking_date >= start_date,
            BookingModel.booking_date <= end_date,
            BookingModel.notes.ilike(f"%{DEMO_MARKER}%"),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None


def pick_status(prepayment_enabled: bool) -> str:
    """Statuses plausible for a booking that hasn't happened yet — no 'completed'."""
    if random.random() < 0.10:
        return "cancelled"  # pre-emptively cancelled ahead of time
    if prepayment_enabled and random.random() < 0.5:
        conv_roll = random.random()
        if conv_roll < 0.75:
            return "confirmed"  # prepayment already confirmed by owner
        elif conv_roll < 0.9:
            return "prepayment_sent"  # client sent, owner hasn't confirmed yet
        else:
            return "pending_prepayment"  # client hasn't paid yet
    if random.random() < 0.15:
        return "pending"  # awaiting owner confirmation
    return "confirmed"


async def seed_business(session, business, tomorrow: date) -> int:
    employees_result = await session.execute(
        select(Employee).where(Employee.business_id == business.id, Employee.is_active == True)
    )
    employees = employees_result.scalars().all()
    if not employees:
        print(f"  business {business.id} ({business.name!r}): no active employees, skipping")
        return 0

    services_result = await session.execute(
        select(Service).where(
            Service.business_id == business.id,
            Service.is_additional == False,
            Service.is_active == True,
        )
    )
    services = services_result.scalars().all()
    if not services:
        print(f"  business {business.id} ({business.name!r}): no active services, skipping")
        return 0

    wh_start = business.working_hours_start or time(9, 0)
    wh_end = business.working_hours_end or time(20, 0)
    prepayment_enabled = bool(business.prepayment_enabled and business.payment_phone)
    prepayment_percent = business.prepayment_percent or 100

    created = 0
    for day_offset in range(0, DAYS_FORWARD):
        booking_date = tomorrow + timedelta(days=day_offset)
        is_weekend = booking_date.weekday() >= 5
        if random.random() < 0.08:
            continue  # occasional fully-idle day

        base_per_employee = 1 if is_weekend else random.randint(2, 4)

        for employee in employees:
            n_bookings = max(0, base_per_employee + random.randint(-1, 1))
            for _ in range(n_bookings):
                service = random.choice(services)
                hour_span = wh_end.hour - wh_start.hour
                if hour_span <= 1:
                    continue
                start_hour = wh_start.hour + random.randint(0, hour_span - 1)
                booking_time = time(start_hour, random.choice([0, 15, 30, 45]))

                status = pick_status(prepayment_enabled)
                has_prepayment = status in ("confirmed", "prepayment_sent", "pending_prepayment") and prepayment_enabled

                client = await get_or_create_guest_user(session, random_client_name(), random_phone())

                # Booked recently (last 0-14 days), independent of the future booking_date.
                created_at = datetime.utcnow() - timedelta(days=random.randint(0, 14), hours=random.randint(0, 23))
                extra_kwargs = {"notes": DEMO_MARKER, "created_at": created_at}

                if has_prepayment:
                    amount = int((service.price or 0) * prepayment_percent / 100)
                    extra_kwargs["prepayment_amount"] = amount
                    if status == "confirmed":
                        extra_kwargs["prepayment_confirmed_at"] = created_at + timedelta(minutes=random.randint(10, 240))

                try:
                    await create_booking(
                        session,
                        business_id=business.id,
                        client_id=client.id,
                        service_id=service.id,
                        employee_id=employee.id,
                        booking_date=booking_date,
                        booking_time=booking_time,
                        status=status,
                        **extra_kwargs,
                    )
                    created += 1
                except ValueError:
                    # Slot conflict against a booking generated earlier this
                    # run — not fatal, demo data doesn't need an exact count.
                    continue

    print(f"  business {business.id} ({business.name!r}): created {created} bookings")
    return created


async def main():
    async with async_session_maker() as session:
        businesses = await find_demo_businesses(session)
        if not businesses:
            print("No demo businesses found (name containing 'пример'/'example'/'demo') — aborting.")
            sys.exit(1)

        print(f"Found {len(businesses)} demo businesses:")
        for b in businesses:
            print(f"  id={b.id} name={b.name!r}")

        tomorrow = date.today() + timedelta(days=1)
        end_date = tomorrow + timedelta(days=DAYS_FORWARD - 1)
        business_ids = [b.id for b in businesses]

        if await already_seeded(session, business_ids, tomorrow, end_date):
            print(f"Bookings tagged {DEMO_MARKER} already exist in this date range — aborting to avoid duplicates.")
            sys.exit(1)

        total = 0
        for business in businesses:
            total += await seed_business(session, business, tomorrow)

        print(f"\nTotal bookings created: {total}")


if __name__ == "__main__":
    asyncio.run(main())
