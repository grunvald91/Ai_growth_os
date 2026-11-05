"""Fill the existing demo business 11 («100boats ДЕМО») with clearly fictional bookings, so the schedule, the
free-window view and the studio dispatcher have something to show.

* Only business 11, only after checking its owner and that its name says ДЕМО. No other business is touched.
* Clients are invented: «имя + (демо)», phone +7 000 000-00-NN, e-mail demo-NN@example.com. No real person's data.
* Rows are written directly: no booking event is sent, so nobody gets a message about them.
* Every booking carries MARKER in its notes, so `remove` deletes exactly these rows (and the invented clients).
* Reminders of the demo business (owner and masters) are switched off while the demo bookings exist, otherwise the
  owner would get a reminder for each of them; `remove` switches them back on.

Modes (env DEMO_MODE): apply (default: dry run, prints the plan) | write | remove | inspect (aggregates only) | confirm_tests.
"""
import asyncio
import os
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BUSINESS_ID = 11
MARKER = '[DEMO-SEED-100BOATS-20261023]'
HOURS = 5
FIRST_START, LAST_START = 9, 16          # a 5-hour rental must end by 21:00
DAYS_BACK, DAYS_FORWARD = 6, 13
CLIENTS = [
    'Анна К.', 'Дмитрий П.', 'Мария С.', 'Игорь В.', 'Елена Т.', 'Сергей Л.', 'Ольга Н.', 'Алексей М.',
    'Наталья Р.', 'Павел Ж.', 'Юлия Б.', 'Андрей Ф.', 'Ирина Г.', 'Максим Д.', 'Татьяна О.', 'Роман Ш.',
]


def plan_bookings(today: date, vessels: list[int], seed: int = 20261023) -> list[dict]:
    """Deterministic list of bookings: {date, hour, vessel, client, status}. Never overlaps on one vessel."""
    rng = random.Random(seed)
    items = []
    for offset in range(-DAYS_BACK, DAYS_FORWARD + 1):
        day = today + timedelta(days=offset)
        busy = 0.65 if day.weekday() >= 5 else 0.38
        for vessel in vessels:
            if rng.random() >= busy:
                continue
            start = rng.choice(range(FIRST_START, LAST_START + 1))
            items.append({'date': day, 'hour': start, 'vessel': vessel, 'client': rng.randrange(len(CLIENTS))})
            # sometimes a second trip the same day, after the first one
            second = start + HOURS
            if second <= LAST_START and rng.random() < 0.15:
                items.append({'date': day, 'hour': second, 'vessel': vessel, 'client': rng.randrange(len(CLIENTS))})
    for item in items:
        item['status'] = 'completed' if item['date'] < today else 'confirmed'
    upcoming = [i for i in items if today + timedelta(days=1) <= i['date'] <= today + timedelta(days=8)]
    rng.shuffle(upcoming)
    for item in upcoming[:3]:
        item['status'] = 'pending'            # new requests waiting for the owner
    for item in upcoming[3:5]:
        item['status'] = 'prepayment_sent'    # the client says he paid; the owner must check
    for item in upcoming[5:7]:
        item['status'] = 'cancelled'
    cancelled_past = [i for i in items if i['date'] < today]
    rng.shuffle(cancelled_past)
    for item in cancelled_past[:2]:
        item['status'] = 'cancelled'
    return sorted(items, key=lambda i: (i['date'], i['hour'], i['vessel']))


async def run(mode: str) -> None:
    from sqlalchemy import delete, func, select
    from database.crud import create_booking
    from database.database import async_session_maker
    from database.models import Booking, Business, Employee, Service, User, VenueTimePricing

    async with async_session_maker() as db:
        business = await db.get(Business, BUSINESS_ID)
        owner = await db.get(User, business.owner_id) if business else None
        if not business or not owner or owner.username != 'Grunvald' or 'ДЕМО' not in (business.name or ''):
            raise RuntimeError('Business 11 is not the expected demo business; refusing')
        vessels = (await db.execute(select(Employee).where(
            Employee.business_id == BUSINESS_ID, Employee.is_active.is_(True)).order_by(Employee.id))).scalars().all()
        main = (await db.execute(select(Service).where(
            Service.business_id == BUSINESS_ID, Service.is_additional.is_(False),
            Service.is_active.is_(True)).order_by(Service.id))).scalars().first()
        tagged = (await db.execute(select(func.count()).select_from(Booking).where(
            Booking.business_id == BUSINESS_ID, Booking.notes.like(f'%{MARKER}%')))).scalar_one()
        other = (await db.execute(select(func.count()).select_from(Booking).where(
            Booking.business_id == BUSINESS_ID, Booking.notes.is_(None) | ~Booking.notes.like(f'%{MARKER}%')))).scalar_one()
        print(f'business=11 vessels={len(vessels)} main_service={"yes" if main else "no"} '
              f'demo_bookings_now={tagged} other_bookings_now={other} '
              f'owner_reminders={business.owner_reminders_enabled} master_reminders={business.master_reminders_enabled}')

        if mode == 'inspect':
            # Aggregates only: no names, phones or notes are printed.
            rows = (await db.execute(select(Booking, User).join(User, User.id == Booking.client_id).where(
                Booking.business_id == BUSINESS_ID))).all()
            by_status, kinds = {}, {}
            for booking, client in rows:
                by_status[booking.status] = by_status.get(booking.status, 0) + 1
                notes = booking.notes or ''
                kind = ('rental_form' if notes.startswith('Аренда') else 'seed_marker' if '[DEMO-SEED' in notes
                        else 'other_notes' if notes else 'no_notes')
                kind += '/real_telegram_client' if (client.telegram_id or 0) > 0 else '/guest_client'
                kinds[kind] = kinds.get(kind, 0) + 1
            shape = {
                'minutes_not_00': sum(1 for b, _ in rows if b.booking_time.minute),
                'with_duration': sum(1 for b, _ in rows if b.duration_minutes),
                'with_tracking_token': sum(1 for b, _ in rows if b.notify_token),
                'with_price': sum(1 for b, _ in rows if b.estimated_price),
                'phone_plus79': sum(1 for _, c in rows if (c.phone or '').startswith('+79')),
                'with_email': sum(1 for _, c in rows if c.email),
                'future_from_today': sum(1 for b, _ in rows if b.booking_date >= datetime.now(ZoneInfo('Europe/Moscow')).date()),
            }
            print('shape=' + str(shape))
            dates = [b.booking_date for b, _ in rows]
            created = [b.created_at for b, _ in rows if b.created_at]
            print('status=' + str(by_status))
            print('kinds=' + str(kinds))
            print(f'booking_dates={min(dates)}..{max(dates)} created_at={min(created)}..{max(created)} '
                  f'distinct_clients={len({c.id for _, c in rows})}')
            return

        if mode == 'confirm_tests':
            # Owner decision of 23.10.2026: the older test requests from today onward are confirmed and kept as
            # test data. Past ones and cancelled ones stay as they are. Only the status changes; no message is sent.
            today = datetime.now(ZoneInfo('Europe/Moscow')).date()
            old = (await db.execute(select(Booking).where(
                Booking.business_id == BUSINESS_ID, Booking.notes.is_not(None), ~Booking.notes.like(f'%{MARKER}%'),
                Booking.booking_date >= today, Booking.status == 'pending'))).scalars().all()
            past_pending = (await db.execute(select(func.count()).select_from(Booking).where(
                Booking.business_id == BUSINESS_ID, ~Booking.notes.like(f'%{MARKER}%'),
                Booking.booking_date < today, Booking.status == 'pending'))).scalar_one()
            for booking in old:
                booking.status = 'confirmed'
            await db.commit()
            print(f'confirmed_old_test_bookings={len(old)} past_pending_left_as_is={past_pending}')
            return

        if mode == 'remove':
            ids = (await db.execute(select(Booking.id).where(
                Booking.business_id == BUSINESS_ID, Booking.notes.like(f'%{MARKER}%')))).scalars().all()
            if ids:
                await db.execute(delete(Booking).where(Booking.id.in_(ids)))
            users = (await db.execute(select(User.id).where(
                User.phone.like('+7000000%'), User.telegram_id < 0))).scalars().all()
            left = [u for u in users if not (await db.execute(select(Booking.id).where(Booking.client_id == u).limit(1))).first()]
            if left:
                await db.execute(delete(User).where(User.id.in_(left)))
            business.owner_reminders_enabled = True
            business.master_reminders_enabled = True
            await db.commit()
            print(f'removed_bookings={len(ids)} removed_demo_clients={len(left)} reminders=on')
            return

        if not vessels or not main:
            raise RuntimeError('No vessels or main service in business 11')
        if tagged:
            raise RuntimeError('Demo bookings already exist; run remove first')
        rates = {}
        for vessel in vessels:
            rule = (await db.execute(select(VenueTimePricing).where(
                VenueTimePricing.employee_id == vessel.id).order_by(VenueTimePricing.id))).scalars().first()
            rates[vessel.id] = rule.price_per_hour * HOURS if rule and rule.price_per_hour else None
        today = datetime.now(ZoneInfo('Europe/Moscow')).date()
        plan = plan_bookings(today, [v.id for v in vessels])
        counts = {}
        for item in plan:
            counts[item['status']] = counts.get(item['status'], 0) + 1
        print(f'plan: total={len(plan)} ' + ' '.join(f'{k}={v}' for k, v in sorted(counts.items())))
        if mode != 'write':
            print('dry_run=yes; production_untouched=yes')
            return

        from database.crud import get_or_create_guest_user
        clients = []
        for index, name in enumerate(CLIENTS, start=1):
            clients.append(await get_or_create_guest_user(
                db, f'{name} (демо)', f'+7000000{index:04d}', f'demo-{index:02d}@example.com'))
        created = skipped = 0
        rng = random.Random(7)
        now = datetime.utcnow()
        for item in plan:
            price = rates[item['vessel']]
            extra = {'notes': f'{MARKER}\nДемо-запись: аренда на {HOURS} ч. Клиент вымышленный.',
                     'duration_minutes': HOURS * 60, 'estimated_price': price,
                     'created_at': now - timedelta(days=rng.randint(1, 9), hours=rng.randint(0, 20))}
            if item['status'] == 'prepayment_sent' and price:
                extra['prepayment_amount'] = int(price * 0.2)
                extra['prepayment_requested_at'] = now - timedelta(hours=rng.randint(2, 30))
            try:
                await create_booking(db, business_id=BUSINESS_ID, client_id=clients[item['client']].id,
                                     service_id=main.id, employee_id=item['vessel'], booking_date=item['date'],
                                     booking_time=time(item['hour']), status=item['status'], **extra)
                created += 1
            except ValueError:
                skipped += 1
        business.owner_reminders_enabled = False
        business.master_reminders_enabled = False
        await db.commit()
        print(f'created={created} skipped_conflicts={skipped} reminders=off')


if __name__ == '__main__':
    asyncio.run(run(os.environ.get('DEMO_MODE', 'apply')))
