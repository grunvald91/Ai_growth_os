"""Configure the existing ServGo business 11 as a five-hour booking demo.

No code deploy, fictional bookings or payments. Read-only by default; --apply
updates only business 11 and its existing nine vessels and five services.
Run on the production host with its existing Python environment/database config.
"""
import argparse
import asyncio
from datetime import time

DEMO_HOURS = 5
VESSEL_RATES = {
    'Princess 45': 19000,
    'Fairline Squadron 58': 39000,
    'Azimut 39': 16000,
    'Sunseeker 72': 59000,
    'Princess 56': 29000,
    'Fazerint 42': 13500,
    'Lavezzi 40': 16000,
    'Dufour Atoll 43': 12000,
    'Bavaria 380 Sport': 12000,
}


def demo_total(vessel_name: str) -> int:
    return VESSEL_RATES[vessel_name] * DEMO_HOURS


def demo_description(vessel_name: str, original: str) -> str:
    description = original.split(' Опубликовано от')[0]
    description = description.split(' Демо-цена за 5 часов:')[0]
    return (description + f' Демо-цена за 5 часов: {demo_total(vessel_name):,} ₽. '
            'Окончательную цену подтверждает владелец.')


async def configure(apply: bool) -> None:
    from sqlalchemy import select, func
    from database.database import async_session_maker
    from database.models import Business, Booking, Employee, EmployeeService, Service, User, VenueTimePricing

    async with async_session_maker() as db:
        business = await db.get(Business, 11)
        if not business:
            raise RuntimeError('Business 11 not found; refusing to create another business')
        owner = await db.get(User, business.owner_id)
        if not owner or owner.username != 'Grunvald':
            raise RuntimeError('Business 11 owner mismatch')
        vessels = (await db.execute(select(Employee).where(Employee.business_id == 11))).scalars().all()
        by_name = {v.name: v for v in vessels}
        if len(vessels) != 9 or set(by_name) != set(VESSEL_RATES):
            raise RuntimeError('Business 11 vessel inventory differs from the nine agreed resources')
        services = (await db.execute(select(Service).where(Service.business_id == 11))).scalars().all()
        main = next((s for s in services if s.name == 'Аренда яхты / катера'), None)
        if not main or len(services) != 5:
            raise RuntimeError('Business 11 service inventory differs from expected five services')
        count = (await db.execute(select(func.count()).select_from(Booking).where(Booking.business_id == 11))).scalar_one()
        if count:
            raise RuntimeError('Business 11 has bookings; refusing to change pricing around existing requests')
        existing_rules = {}
        for vessel in vessels:
            rules = (await db.execute(select(VenueTimePricing).where(
                VenueTimePricing.employee_id == vessel.id))).scalars().all()
            if len(rules) > 1:
                raise RuntimeError(f'Vessel {vessel.id} has multiple pricing rules; refusing to overwrite')
            existing_rules[vessel.id] = rules[0] if rules else None
        print(f'preflight_business_id=11; owner_verified=yes; active={business.is_active}; '
              f'main_service_minutes={main.duration_minutes}; vessels={len(vessels)}; '
              f'services={len(services)}; existing_price_rules={sum(r is not None for r in existing_rules.values())}; '
              f'existing_bookings={count}; apply={apply}')
        if not apply:
            for name in VESSEL_RATES:
                print(f'demo_price_5h: {name}: {demo_total(name)} RUB')
            return

        business.name = '100boats ДЕМО — аренда яхт и катеров'
        business.business_type = 'venue_rental'
        business.is_active = True
        business.address = 'Россия, Краснодарский край, Сочи, ул. Несебрская, 6, 354340'
        business.phone = '+7 (995) 00-599-01'
        business.description = (
            'Неофициальная демонстрационная карточка ServGo, не интеграция с 100boats. '
            'Выберите судно, дату и время и отправьте заявку на демонстрационную аренду 5 часов. '
            'Цена для выбранного судна показана как пример и подтверждается владельцем. '
            'Показанные свободные часы относятся только к заявкам ServGo, а не к фактическому расписанию флота. '
            'Размер предоплаты — до 20%. Окончательная сумма подтверждается при бронировании. '
            'После подтверждения капитан свяжется для уточнения места и времени встречи.'
        )
        business.directions = 'Офис: ул. Несебрская, 6. WhatsApp 24/7 — горячая линия, не расписание судов.'
        business.cancellation_policy_text = (
            'За 7 дней — без удержаний; при неявке предоплата не возвращается. '
            'При небезопасной погоде или закрытом порте — перенос; '
            'при отказе от переноса из-за погоды — возврат.'
        )
        business.booking_interval_minutes = 60
        business.working_hours_start = time(9)
        business.working_hours_end = time(21)
        business.prepayment_enabled = False
        business.prepayment_percent = None
        business.payment_details = 'Предоплата до 20%, после индивидуального подтверждения. Онлайн-оплаты в демо нет.'
        main.duration_minutes = DEMO_HOURS * 60
        main.price = None  # Price is vessel-specific, from VenueTimePricing.
        main.is_active = True
        main.is_additional = False
        main.is_hourly_price = False
        main.description = 'Демо-заявка на аренду выбранного судна на 5 часов; цена зависит от судна.'
        main.tariff_description = ('Только демонстрационная аренда на 5 часов. Цена указана как пример '
                                   'для выбранного судна, не является тарифом 100boats.')
        for name, vessel in by_name.items():
            vessel.is_active = True
            vessel.booking_interval_minutes = 60
            vessel.min_booking_minutes = DEMO_HOURS * 60
            vessel.solo_price_per_hour = None  # Avoid a competing pricing mode in the existing UI.
            vessel.description = demo_description(name, vessel.description or '')
            rule = existing_rules[vessel.id]
            if rule is None:
                rule = VenueTimePricing(employee_id=vessel.id)
                db.add(rule)
            rule.time_from = time(9)
            rule.time_to = time(21)
            rule.day_type = 'all'
            rule.price_per_hour = VESSEL_RATES[name]
            link = (await db.execute(select(EmployeeService).where(
                EmployeeService.employee_id == vessel.id, EmployeeService.service_id == main.id
            ))).scalar_one_or_none()
            if link is None:
                db.add(EmployeeService(employee_id=vessel.id, service_id=main.id))
        await db.commit()
        print(f'updated_business_id=11; active=yes; five_hour_service=yes; '
              f'vessel_price_rules={len(vessels)}; existing_bookings=0; auto_payment=no')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='Write the demo configuration to business 11')
    args = parser.parse_args()
    asyncio.run(configure(args.apply))
