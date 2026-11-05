"""Create or update the working 100boats ДЕМО rental business in ServGo.

Run on the ServGo host from /opt/booking_bot with ./venv/bin/python.
"""
import asyncio
from datetime import time

from sqlalchemy import select
from database.database import async_session_maker
from database.models import Business, BusinessOwner, Employee, EmployeeService, Service, User

NAME = '100boats ДЕМО — аренда яхт и катеров'
LEGACY_NAME = '100boats — демонстрация ServGo'
OWNER_USERNAME = 'Grunvald'
CONDITIONS = (
    'Опубликованные почасовые тарифы применяются при аренде от 5 часов. '
    'Для аренды 1–4 часа и дополнительных услуг с индивидуальной ценой '
    'менеджер подтверждает сумму заявки. Предоплата — до 20%; конкретную '
    'сумму подтверждает владелец. После подтверждения капитан уточнит место встречи.'
)
VESSELS = [
    ('Princess 45', 'Моторная яхта VIP', 19000, 3, 6, '14 м', '4,27 м', 'Великобритания', '61 км/ч', None,
     'Двухпалубная моторная яхта VIP-класса с флайбриджем.'),
    ('Fairline Squadron 58', 'Моторная яхта Luxury', 39000, 3, 6, '19 м', '5 м', 'Великобритания', '60 км/ч', None,
     'Большая моторная яхта с флайбриджем и просторной развлекательной зоной. Возможна установка гидроциклов.'),
    ('Azimut 39', 'Моторная яхта', 16000, 2, 6, '11 м', '4 м', 'Италия', '55 км/ч', None,
     'Моторная яхта с флайбриджем для семейного отдыха, праздников, вечеринок и рыбалки.'),
    ('Sunseeker 72', 'VIP-моторная яхта', 59000, 4, 8, '25 м', '6 м', 'Великобритания', '75 км/ч', None,
     'Одна из крупнейших яхт каталога, со стабилизатором качки, просторным салоном, каютами и водным бассейном.'),
    ('Princess 56', 'Премиум-моторная яхта', 29000, 4, 8, '19 м', '5 м', 'Великобритания', '55 км/ч', 11,
     'Большая премиум-яхта с флайбриджем, просторным салоном и зонами для отдыха и загара.'),
    ('Fazerint 42', 'Моторная яхта', 13500, 3, 6, '13 м', '3 м', 'Россия', '50 км/ч', None,
     'Скоростная яхта для небольшой компании или семейной прогулки. Выход из Имеретинской марины.'),
    ('Lavezzi 40', 'Парусный катамаран', 16000, 4, 6, '13 м', '6 м', 'Италия', '20 км/ч', None,
     'Просторный катамаран с большой площадью палубы, сетками для отдыха над водой и парусным форматом.'),
    ('Dufour Atoll 43', 'Парусная яхта', 12000, 2, 6, '13 м', '4,65 м', 'Франция', '10 км/ч', 12,
     'Парусная яхта, рассчитанная на чартер, с зонами отдыха, акустической системой и панорамной крышей.'),
    ('Bavaria 380 Sport', 'Моторный катер', 12000, 2, 6, '12 м', '4 м', 'Германия', '65 км/ч', 11,
     'Скоростной катер для морских прогулок. Капитан и топливо входят в стоимость.'),
]

async def main():
    async with async_session_maker() as db:
        owner = (await db.execute(select(User).where(User.username == OWNER_USERNAME))).scalar_one()
        existing = (await db.execute(select(Business).where(Business.name.in_((NAME, LEGACY_NAME))))).scalar_one_or_none()
        business = existing or Business(owner_id=owner.id, name=NAME, business_type='venue_rental')
        if existing and business.owner_id != owner.id:
            raise RuntimeError('Existing demo business belongs to a different owner')
        business.name = NAME
        business.description = ('Аренда яхт и катеров в Сочи и Имеретинке. '
                                'Выберите судно, дату, длительность и отправьте заявку.\n\n' + CONDITIONS)
        business.address = 'Россия, Краснодарский край, Сочи, ул. Несебрская, 6, 354340'
        business.phone = '+7 (995) 00-599-01'
        business.directions = ('Офис: ул. Несебрская, 6. WhatsApp 24/7 — горячая линия, '
                               'не график выхода судов.')
        business.booking_interval_minutes = 60
        business.working_hours_start = time(9)
        business.working_hours_end = time(21)
        business.prepayment_enabled = False  # No automatic charge; owner sets <=20% per request.
        business.prepayment_percent = None
        business.payment_details = ('Размер предоплаты — до 20%. Окончательную сумму сообщает владелец '
                                    'после подтверждения заявки; оплата не производится на сайте.')
        business.cancellation_policy_text = ('Отмена за 7 дней — без удержаний; при неявке предоплата не возвращается. '
            'При небезопасной погоде или закрытом порте предлагается перенос; при отказе от переноса из-за погоды — возврат денег.')
        db.add(business)
        await db.flush()
        existing_owner = (await db.execute(select(BusinessOwner.id).where(
            BusinessOwner.business_id == business.id, BusinessOwner.user_id == owner.id))).scalar_one_or_none()
        if existing_owner is None:
            db.add(BusinessOwner(business_id=business.id, user_id=owner.id, role='owner', added_by_id=owner.id))
        service = (await db.execute(select(Service).where(Service.business_id == business.id,
            Service.name == 'Аренда яхты / катера'))).scalar_one_or_none()
        if service is None:
            service = Service(business_id=business.id, name='Аренда яхты / катера')
        service.duration_minutes = 60
        service.price = None
        service.is_active = True
        service.is_additional = False
        service.is_hourly_price = False
        service.tariff_description = CONDITIONS
        service.sort_order = 0
        db.add(service)
        await db.flush()
        for name, kind, rate, cabins, beds, length, width, country, speed, capacity, description in VESSELS:
            vessel = (await db.execute(select(Employee).where(
                Employee.business_id == business.id, Employee.name == name))).scalar_one_or_none()
            if vessel is None:
                vessel = Employee(business_id=business.id, name=name, role='master')
            vessel.is_active = True
            vessel.specialization = kind
            vessel.booking_interval_minutes = 60
            vessel.capacity = capacity
            vessel.description = (f'{description}\nКаюты: {cabins}; спальных мест: {beds}; длина: {length}; '
                                  f'ширина: {width}; страна: {country}; максимальная скорость: {speed}. '
                                  f'{rate:,} ₽/час при аренде от 5 часов.')
            vessel.min_booking_minutes = 60
            vessel.solo_price_per_hour = rate
            db.add(vessel)
            await db.flush()
            link = (await db.execute(select(EmployeeService.employee_id).where(
                EmployeeService.employee_id == vessel.id, EmployeeService.service_id == service.id))).scalar_one_or_none()
            if link is None:
                db.add(EmployeeService(employee_id=vessel.id, service_id=service.id))
        addons = [
            ('Фотосессия / видеоролик на яхте', 15000, 'Фотосъёмка и видеоролик.'),
            ('Премиальный трансфер из отеля', 5000, 'Mercedes-Maybach, Mercedes S-Class, Mercedes V-Class до 7 мест.'),
            ('Спорткар / кабриолет', None, 'От 25 000 ₽; индивидуальная цена подтверждается.'),
            ('Вертолётная прогулка / трансфер', None, 'От 25 000 ₽; индивидуальная цена подтверждается.'),
        ]
        for index, (name, price, description) in enumerate(addons, 1):
            addon = (await db.execute(select(Service).where(
                Service.business_id == business.id, Service.name == name))).scalar_one_or_none()
            if addon is None:
                addon = Service(business_id=business.id, name=name)
            addon.description = description
            addon.duration_minutes = 0
            addon.price = price
            addon.is_active = True
            addon.is_additional = True
            addon.is_hourly_price = False
            addon.sort_order = index
            db.add(addon)
        # Activate only after deployment has installed the rental API and DB columns.
        business.is_active = True
        await db.commit()
        print(f'working_demo_business_id={business.id}; vessels={len(VESSELS)}; addons={len(addons)}; active={business.is_active}')

if __name__ == '__main__':
    asyncio.run(main())
