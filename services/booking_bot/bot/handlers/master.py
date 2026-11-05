"""
Обработчики для панели мастеров
"""
import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import BaseFilter
from datetime import datetime, date, time, timedelta

from sqlalchemy import select

logger = logging.getLogger(__name__)

from database import async_session_maker
from database.crud import (
    get_employee_by_telegram_id,
    get_business_by_id,
    get_business_services,
    get_service_by_id,
    create_booking,
    get_or_create_user,
    get_or_create_guest_user,
    get_business_bookings,
    get_master_bookings,
    update_booking_status,
    get_user_businesses
)
from bot.states.master import ManualBookingStates
from bot.utils.recurring import compute_recurring_dates, build_visit_slots
from bot.keyboards import master_kb
from bot.keyboards.client_kb import get_main_menu_keyboard
from bot.utils.validators import validate_phone, format_date_with_weekday, format_phone
from bot.config import ADMIN_IDS
from bot.services.notifications import notify_administrator_new_booking
from bot.services.mlm_sync import can_be_business_owner

router = Router()


class MasterModeFilter(BaseFilter):
    """Фильтр для проверки, что пользователь в режиме мастера"""
    async def __call__(self, message: Message, state: FSMContext) -> bool:
        data = await state.get_data()
        return data.get("current_mode") == "master"

# Русские названия дней недели (сокращённые)
WEEKDAY_NAMES = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']


# ===== ГЛАВНОЕ МЕНЮ МАСТЕРА =====

@router.message(F.text == "➕ Записать клиента", MasterModeFilter())
async def start_manual_booking(message: Message, state: FSMContext):
    """Начало ручной записи клиента мастером"""
    async with async_session_maker() as session:
        # Проверяем, является ли пользователь мастером
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

        if not employee:
            user = await get_or_create_user(
                session,
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            businesses = await get_user_businesses(session, user.id)
            is_admin = message.from_user.id in ADMIN_IDS
            mlm_owner = await can_be_business_owner(message.from_user.id)
            show_admin = is_admin or len(businesses) > 0 or mlm_owner

            await message.answer(
                "❌ У вас нет доступа к этой функции.\n"
                "Попросите владельца бизнеса добавить ваш Telegram ID.",
                reply_markup=get_main_menu_keyboard(show_owner_button=show_admin, show_employee_button=False)
            )
            return

        # Получаем услуги бизнеса
        services = await get_business_services(session, employee.business_id)
        business = await get_business_by_id(session, employee.business_id)

    if not services:
        await message.answer(
            "❌ В вашем бизнесе пока нет услуг.\n"
            "Попросите владельца добавить услуги.",
            reply_markup=master_kb.get_master_menu_keyboard()
        )
        return

    await state.update_data(
        employee_id=employee.id,
        business_id=employee.business_id,
        business_name=business.name,
        selected_service_ids=[]
    )

    await message.answer(
        f"📝 Ручная запись клиента\n🏢 {business.name}\n\nВыберите одну или несколько услуг:",
        reply_markup=master_kb.get_services_for_booking_keyboard(services, selected_ids=[])
    )
    await state.set_state(ManualBookingStates.choosing_service)


@router.callback_query(F.data.startswith("master_service:"), ManualBookingStates.choosing_service)
async def service_chosen_by_master(callback: CallbackQuery, state: FSMContext):
    """Выбор услуги мастером (множественный выбор)"""
    service_id = int(callback.data.split(":")[1])

    data = await state.get_data()
    business_id = data["business_id"]
    business_name = data["business_name"]
    selected_service_ids = data.get("selected_service_ids", [])

    # Переключаем выбор услуги (добавляем или удаляем)
    if service_id in selected_service_ids:
        selected_service_ids.remove(service_id)
    else:
        selected_service_ids.append(service_id)

    await state.update_data(selected_service_ids=selected_service_ids)

    # Обновляем клавиатуру с новым состоянием выбора
    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)

    await callback.message.edit_text(
        f"📝 Ручная запись клиента\n🏢 {business_name}\n\nВыберите одну или несколько услуг:",
        reply_markup=master_kb.get_services_for_booking_keyboard(services, selected_ids=selected_service_ids)
    )
    await callback.answer()


@router.callback_query(F.data == "master_services_done", ManualBookingStates.choosing_service)
async def master_services_selection_done(callback: CallbackQuery, state: FSMContext):
    """Завершение выбора услуг мастером"""
    data = await state.get_data()
    selected_service_ids = data.get("selected_service_ids", [])

    if not selected_service_ids:
        await callback.answer("Выберите хотя бы одну услугу", show_alert=True)
        return

    # Получаем информацию о выбранных услугах
    async with async_session_maker() as session:
        services = []
        total_duration = 0
        total_price = 0
        for service_id in selected_service_ids:
            service = await get_service_by_id(session, service_id)
            services.append(service)
            total_duration += service.duration_minutes
            if service.price:
                total_price += service.price

    # Сохраняем информацию о выбранных услугах
    services_info = []
    for service in services:
        services_info.append({
            "id": service.id,
            "name": service.name,
            "duration": service.duration_minutes,
            "price": service.price
        })

    await state.update_data(
        services_info=services_info,
        total_duration=total_duration,
        total_price=total_price
    )

    # Формируем текст с выбранными услугами
    services_text = "\n".join([f"• {s.name} ({s.duration_minutes} мин)" for s in services])
    price_text = f"\n💰 Общая стоимость: {total_price}₽" if total_price > 0 else ""

    await callback.message.edit_text(
        f"✅ Выбранные услуги:\n{services_text}\n"
        f"⏱ Общая длительность: {total_duration} мин{price_text}\n\n"
        "Введите имя клиента:"
    )
    await state.set_state(ManualBookingStates.entering_client_name)
    await callback.answer()


@router.message(ManualBookingStates.entering_client_name)
async def client_name_entered_by_master(message: Message, state: FSMContext):
    """Получение имени клиента"""
    await state.update_data(client_name=message.text)

    await message.answer(
        "Введите номер телефона клиента:\n"
        "Формат: +7XXXXXXXXXX или 8XXXXXXXXXX"
    )
    await state.set_state(ManualBookingStates.entering_client_phone)


@router.message(ManualBookingStates.entering_client_phone)
async def client_phone_entered_by_master(message: Message, state: FSMContext):
    """Получение телефона клиента"""
    if not validate_phone(message.text):
        await message.answer(
            "❌ Неверный формат телефона!\n"
            "Используйте формат: +7XXXXXXXXXX или 8XXXXXXXXXX\n\n"
            "Попробуйте снова:"
        )
        return

    # Форматируем телефон: если 10 цифр - добавляем +7
    formatted_phone = format_phone(message.text)
    await state.update_data(client_phone=formatted_phone)

    # Генерируем даты для выбора (на месяц вперед)
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    
    for i in range(30):
        future_date = date.today() + timedelta(days=i)
        day_num = future_date.day
        weekday = WEEKDAY_NAMES[future_date.weekday()]
        date_str = f"{day_num} {weekday}"
        builder.button(text=date_str, callback_data=f"master_date:{i}")

    builder.adjust(4)

    await message.answer(
        "Выберите дату записи:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ManualBookingStates.choosing_date)


@router.callback_query(F.data.startswith("master_date:"), ManualBookingStates.choosing_date)
async def date_chosen_by_master(callback: CallbackQuery, state: FSMContext):
    """Выбор даты мастером"""
    days_offset = int(callback.data.split(":")[1])
    booking_date = date.today() + timedelta(days=days_offset)

    await state.update_data(booking_date=booking_date.strftime("%d.%m.%Y"))

    data = await state.get_data()

    # Генерируем доступное время
    async with async_session_maker() as session:
        business = await get_business_by_id(session, data['business_id'])
        employee_id = data['employee_id']

        # Получаем сотрудника для проверки индивидуального интервала
        from database.crud import get_business_employees
        employees = await get_business_employees(session, data['business_id'])
        employee = None
        for emp in employees:
            if emp.id == employee_id:
                employee = emp
                break

        # Определяем шаг записи
        interval = business.booking_interval_minutes if business.booking_interval_minutes else 30
        if employee and employee.booking_interval_minutes:
            interval = employee.booking_interval_minutes

        # Получаем существующие записи на эту дату
        existing_bookings = await get_business_bookings(session, data['business_id'], booking_date)

    # Генерируем временные слоты
    if not business.working_hours_start or not business.working_hours_end:
        start_hour, start_min = 9, 0
        end_hour, end_min = 18, 0
    else:
        start_hour = business.working_hours_start.hour
        start_min = business.working_hours_start.minute
        end_hour = business.working_hours_end.hour
        end_min = business.working_hours_end.minute

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    current_time = datetime.now().time()
    start_time = time(start_hour, start_min)
    end_time = time(end_hour, end_min)

    # Генерируем слоты
    current_hour = start_hour
    current_min = start_min

    while True:
        # Проверяем переполнение часов (защита от hour >= 24)
        if current_hour >= 24:
            break

        slot_time = time(current_hour, current_min)

        # Проверяем, не вышли ли за рабочее время
        if slot_time >= end_time:
            break

        # Для сегодняшней даты пропускаем прошедшее время
        if days_offset == 0 and slot_time <= current_time:
            current_min += interval
            if current_min >= 60:
                current_hour += current_min // 60
                current_min = current_min % 60
            continue

        # Проверяем перерыв
        is_break = False
        if business.break_start and business.break_end:
            if business.break_start <= slot_time < business.break_end:
                is_break = True

        # Проверяем, не занято ли время
        is_booked = False
        for booking in existing_bookings:
            if booking.employee_id == employee_id and booking.booking_time == slot_time:
                if booking.status in ['pending', 'confirmed']:
                    is_booked = True
                    break

        if not is_break and not is_booked:
            builder.button(
                text=slot_time.strftime('%H:%M'),
                callback_data=f"master_time:{slot_time.strftime('%H:%M')}"
            )

        current_min += interval
        if current_min >= 60:
            current_hour += current_min // 60
            current_min = current_min % 60

    builder.adjust(3)

    await callback.message.edit_text(
        f"Дата: {format_date_with_weekday(booking_date)}\n\nВыберите время:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ManualBookingStates.choosing_time)
    await callback.answer()


@router.callback_query(F.data.startswith("master_time:"), ManualBookingStates.choosing_time)
async def time_chosen_by_master(callback: CallbackQuery, state: FSMContext):
    """Выбор времени мастером — переход к выбору повтора записи"""
    time_str = callback.data.split(":")[1] + ":" + callback.data.split(":")[2]
    await state.update_data(booking_time=time_str)
    await state.set_state(ManualBookingStates.choosing_repeat)

    await callback.message.edit_text(
        "Повторять эту запись каждую неделю?",
        reply_markup=master_kb.get_master_repeat_toggle_keyboard()
    )
    await callback.answer()


async def show_master_confirmation(message: Message, state: FSMContext):
    """Показ экрана подтверждения ручной записи мастером"""
    data = await state.get_data()

    services_info = data.get('services_info', [])
    if services_info:
        services_text = "\n".join(
            f"  • {s['name']} ({s['duration']} мин" +
            (f", {s['price']}₽" if s['price'] else "") + ")"
            for s in services_info
        )
        total_duration = data.get('total_duration', 0)
        total_price = data.get('total_price', 0)
        price_text = f"💰 Общая стоимость: {total_price}₽\n" if total_price > 0 else ""
    else:
        # Обратная совместимость со старым форматом (одна услуга)
        price_part = f", {data['service_price']}₽" if data.get('service_price') else ""
        services_text = f"  • {data['service_name']} ({data['service_duration']} мин{price_part})"
        total_duration = data['service_duration']
        total_price = data.get('service_price', 0)
        price_text = f"💰 Цена: {total_price}₽\n" if total_price else ""

    repeat_weeks = data.get('repeat_weeks')
    repeat_line = f"🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю\n" if repeat_weeks and repeat_weeks > 1 else ""

    confirmation_text = f"""
📋 <b>Подтверждение записи</b>

🏢 Заведение: {data['business_name']}
💼 Услуги:
{services_text}
⏱ Общая длительность: {total_duration} мин
{price_text}{repeat_line}👤 Клиент: {data['client_name']}
📞 Телефон: {data['client_phone']}
📅 Дата: {data['booking_date']}
🕐 Время: {data['booking_time']}

Подтверждаете запись?
"""

    await message.edit_text(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=master_kb.get_confirmation_keyboard_master()
    )
    await state.set_state(ManualBookingStates.confirming)


@router.callback_query(F.data == "master_repeat_skip", ManualBookingStates.choosing_repeat)
async def master_repeat_skip_chosen(callback: CallbackQuery, state: FSMContext):
    """Мастер отказался от повтора"""
    await state.update_data(repeat_weeks=None)
    await show_master_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "master_repeat_enable", ManualBookingStates.choosing_repeat)
async def master_repeat_enable_chosen(callback: CallbackQuery, state: FSMContext):
    """Мастер включил повтор — показываем степпер"""
    await state.update_data(repeat_weeks_draft=2)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=master_kb.get_master_repeat_stepper_keyboard(2)
    )
    await callback.answer()


@router.callback_query(F.data.in_(["master_repeat_dec", "master_repeat_inc"]), ManualBookingStates.choosing_repeat)
async def master_repeat_stepper_changed(callback: CallbackQuery, state: FSMContext):
    """Изменение числа недель повтора степпером"""
    data = await state.get_data()
    weeks = data.get('repeat_weeks_draft', 2)
    if callback.data == "master_repeat_dec":
        weeks = max(2, weeks - 1)
    else:
        weeks = min(8, weeks + 1)
    await state.update_data(repeat_weeks_draft=weeks)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=master_kb.get_master_repeat_stepper_keyboard(weeks)
    )
    await callback.answer()


@router.callback_query(F.data == "master_repeat_noop", ManualBookingStates.choosing_repeat)
async def master_repeat_noop_pressed(callback: CallbackQuery, state: FSMContext):
    """Нажатие на лейбл степпера — просто гасим спиннер"""
    await callback.answer()


@router.callback_query(F.data == "master_repeat_cancel", ManualBookingStates.choosing_repeat)
async def master_repeat_stepper_cancelled(callback: CallbackQuery, state: FSMContext):
    """Возврат от степпера к выбору без повтора / повторять"""
    await callback.message.edit_text(
        "Повторять эту запись каждую неделю?",
        reply_markup=master_kb.get_master_repeat_toggle_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "master_repeat_done", ManualBookingStates.choosing_repeat)
async def master_repeat_stepper_done(callback: CallbackQuery, state: FSMContext):
    """Подтверждение числа недель — переходим к подтверждению записи"""
    data = await state.get_data()
    await state.update_data(repeat_weeks=data.get('repeat_weeks_draft', 2))
    await show_master_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "confirm_manual_booking", ManualBookingStates.confirming)
async def confirm_manual_booking(callback: CallbackQuery, state: FSMContext):
    """Подтверждение ручной записи (с поддержкой повтора по неделям)"""
    data = await state.get_data()

    # Парсим дату и время
    booking_date = datetime.strptime(data['booking_date'], "%d.%m.%Y").date()
    booking_time = datetime.strptime(data['booking_time'], "%H:%M").time()
    repeat_weeks = data.get('repeat_weeks') or 1
    occurrence_dates = compute_recurring_dates(booking_date, repeat_weeks)

    async with async_session_maker() as session:
        # Создаем или находим гостевого клиента по телефону (один клиент на всю серию)
        user = await get_or_create_guest_user(
            session,
            full_name=data['client_name'],
            phone=data.get('client_phone'),
        )

        # Получаем информацию о выбранных услугах
        services_info = data.get('services_info', [])
        if services_info:
            visit_slots = build_visit_slots(services_info, booking_time)
        else:
            # Обратная совместимость - одна услуга (старый формат)
            visit_slots = [(data['service_id'], booking_time)]

        from database.crud import check_series_conflicts
        conflicts = await check_series_conflicts(session, data['employee_id'], occurrence_dates, visit_slots)
        if conflicts:
            conflict_text = ", ".join(d.strftime("%d.%m") for d in conflicts)
            await callback.message.edit_text(
                f"❌ Не удалось создать серию: слот занят на даты: {conflict_text}.\n\n"
                "Ничего не создано — выберите другое время или уменьшите число повторов.",
                reply_markup=None
            )
            await callback.answer()
            await state.clear()
            return

        created_bookings = []
        try:
            for occurrence_date in occurrence_dates:
                for service_id, start_time in visit_slots:
                    booking = await create_booking(
                        session,
                        business_id=data['business_id'],
                        client_id=user.id,
                        service_id=service_id,
                        booking_date=occurrence_date,
                        booking_time=start_time,
                        employee_id=data['employee_id']
                    )
                    created_bookings.append(booking)
        except ValueError as e:
            if created_bookings:
                from sqlalchemy import delete as sa_delete
                from database.models import Booking
                await session.execute(
                    sa_delete(Booking).where(Booking.id.in_([b.id for b in created_bookings]))
                )
                await session.commit()
            await callback.message.edit_text(
                f"❌ К сожалению, {str(e)}.\n\nПопробуйте выбрать другое время.",
                reply_markup=None
            )
            await callback.answer()
            await state.clear()
            return

        # Сохраняем ID первой записи первого визита для уведомления
        first_visit_bookings = created_bookings[:len(visit_slots)]
        first_booking_id = first_visit_bookings[0].id

    # Отправляем уведомление администратору (по первому визиту серии)
    try:
        from sqlalchemy.orm import selectinload
        from database.models import Booking

        async with async_session_maker() as session:
            # Загружаем запись с полными данными
            result = await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.business),
                    selectinload(Booking.client),
                    selectinload(Booking.service),
                    selectinload(Booking.employee)
                )
                .where(Booking.id == first_booking_id)
            )
            booking_with_data = result.scalar_one_or_none()

            if booking_with_data:
                master_name = booking_with_data.employee.name if booking_with_data.employee else None
                await notify_administrator_new_booking(
                    callback.bot,
                    booking_with_data,
                    services_list=services_info if services_info else None,
                    created_by_master=master_name
                )
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Error sending admin notification: {e}")

    # Получаем роли для клавиатуры
    async with async_session_maker() as session:
        user_obj = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user_obj.id)

    is_admin = callback.from_user.id in ADMIN_IDS
    mlm_owner = await can_be_business_owner(callback.from_user.id)
    show_admin = is_admin or len(businesses) > 0 or mlm_owner

    # Удаляем сообщение с подтверждением
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Формируем текст с услугами
    services_info = data.get('services_info', [])
    if services_info:
        services_text = "\n".join([f"  • {s['name']}" for s in services_info])
        services_display = f"💼 Услуги:\n{services_text}"
    else:
        services_display = f"💼 {data.get('service_name', 'Услуга')}"

    repeat_line = f"\n🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю" if repeat_weeks > 1 else ""

    # Отправляем новое сообщение с результатом
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text=f"✅ <b>Запись успешно создана!</b>\n\n"
             f"📅 {data['booking_date']} в {data['booking_time']}\n"
             f"{services_display}\n"
             f"👤 Клиент: {data['client_name']}\n"
             f"📞 {data['client_phone']}"
             f"{repeat_line}\n\n"
             f"👨‍💼 Вы в режиме мастера",
        parse_mode="HTML",
        reply_markup=master_kb.get_master_menu_keyboard(show_owner_button=show_admin)
    )

    await state.clear()
    await callback.answer("Запись создана!")


@router.callback_query(F.data == "cancel_manual_booking", ManualBookingStates.confirming)
async def cancel_manual_booking(callback: CallbackQuery, state: FSMContext):
    """Отмена ручной записи"""
    # Получаем роли для клавиатуры
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    is_admin = callback.from_user.id in ADMIN_IDS
    mlm_owner = await can_be_business_owner(callback.from_user.id)
    show_admin = is_admin or len(businesses) > 0 or mlm_owner

    # Удаляем сообщение
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Отправляем новое сообщение
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="❌ Запись отменена.\n\n👨‍💼 Вы в режиме мастера",
        reply_markup=master_kb.get_master_menu_keyboard(show_owner_button=show_admin)
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "cancel_master_booking_flow")
async def cancel_booking_flow(callback: CallbackQuery, state: FSMContext):
    """Отмена процесса записи на любом шаге"""
    # Получаем роли для клавиатуры
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    is_admin = callback.from_user.id in ADMIN_IDS
    mlm_owner = await can_be_business_owner(callback.from_user.id)
    show_admin = is_admin or len(businesses) > 0 or mlm_owner

    # Удаляем сообщение
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Отправляем новое сообщение
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="❌ Запись отменена.\n\n👨‍💼 Вы в режиме мастера",
        reply_markup=master_kb.get_master_menu_keyboard(show_owner_button=show_admin)
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "back_to_master_time_selection", ManualBookingStates.confirming)
async def back_to_time_from_confirmation(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору времени из подтверждения"""
    data = await state.get_data()

    # Возвращаемся к выбору времени - нужно перегенерировать временные слоты
    booking_date = datetime.strptime(data['booking_date'], "%d.%m.%Y").date()
    days_offset = (booking_date - date.today()).days

    async with async_session_maker() as session:
        business = await get_business_by_id(session, data['business_id'])
        employee_id = data['employee_id']

        from database.crud import get_business_employees
        employees = await get_business_employees(session, data['business_id'])
        employee = None
        for emp in employees:
            if emp.id == employee_id:
                employee = emp
                break

        interval = business.booking_interval_minutes if business.booking_interval_minutes else 30
        if employee and employee.booking_interval_minutes:
            interval = employee.booking_interval_minutes

        existing_bookings = await get_business_bookings(session, data['business_id'], booking_date)

    # Генерируем временные слоты (тот же код, что и в date_chosen_by_master)
    if not business.working_hours_start or not business.working_hours_end:
        start_hour, start_min = 9, 0
        end_hour, end_min = 18, 0
    else:
        start_hour = business.working_hours_start.hour
        start_min = business.working_hours_start.minute
        end_hour = business.working_hours_end.hour
        end_min = business.working_hours_end.minute

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    current_time = datetime.now().time()
    start_time = time(start_hour, start_min)
    end_time = time(end_hour, end_min)

    current_hour = start_hour
    current_min = start_min

    while True:
        slot_time = time(current_hour, current_min)

        if slot_time >= end_time:
            break

        if days_offset == 0 and slot_time <= current_time:
            current_min += interval
            if current_min >= 60:
                current_hour += current_min // 60
                current_min = current_min % 60
            continue

        is_break = False
        if business.break_start and business.break_end:
            if business.break_start <= slot_time < business.break_end:
                is_break = True

        is_booked = False
        for booking in existing_bookings:
            if booking.employee_id == employee_id and booking.booking_time == slot_time:
                if booking.status in ['pending', 'confirmed']:
                    is_booked = True
                    break

        if not is_break and not is_booked:
            builder.button(
                text=slot_time.strftime('%H:%M'),
                callback_data=f"master_time:{slot_time.strftime('%H:%M')}"
            )

        current_min += interval
        if current_min >= 60:
            current_hour += current_min // 60
            current_min = current_min % 60

    builder.adjust(3)

    await callback.message.edit_text(
        f"Дата: {format_date_with_weekday(booking_date)}\n\nВыберите время:",
        reply_markup=builder.as_markup()
    )
    await state.set_state(ManualBookingStates.choosing_time)
    await callback.answer()


@router.message(F.text == "📋 Мои записи на сегодня")
async def show_master_bookings_today(message: Message):
    """Показать записи мастера на сегодня"""
    async with async_session_maker() as session:
        # Проверяем, является ли пользователь мастером
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

        if not employee:
            user = await get_or_create_user(
                session,
                telegram_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name
            )
            businesses = await get_user_businesses(session, user.id)
            is_admin = message.from_user.id in ADMIN_IDS
            mlm_owner = await can_be_business_owner(message.from_user.id)
            show_admin = is_admin or len(businesses) > 0 or mlm_owner

            await message.answer(
                "❌ У вас нет доступа к этой функции.",
                reply_markup=get_main_menu_keyboard(show_owner_button=show_admin, show_employee_button=False)
            )
            return

        # Получаем записи на сегодня
        today = date.today()
        bookings = await get_business_bookings(session, employee.business_id, today)

        # Фильтруем только записи к этому мастеру
        my_bookings = [b for b in bookings if b.employee_id == employee.id]

    if not my_bookings:
        await message.answer(
            "📋 У вас нет записей на сегодня",
            reply_markup=master_kb.get_master_menu_keyboard()
        )
        return

    # Формируем текст со списком записей
    bookings_text = f"📋 <b>Ваши записи на {format_date_with_weekday(today)}</b>\n\n"

    for booking in sorted(my_bookings, key=lambda b: b.booking_time):
        status_emoji = "✅" if booking.status == "confirmed" else "⏳"
        bookings_text += f"{status_emoji} <b>{booking.booking_time.strftime('%H:%M')}</b> - {booking.client.full_name}\n"
        bookings_text += f"   💼 {booking.service.name}\n"
        if booking.client.phone:
            bookings_text += f"   📞 {format_phone(booking.client.phone)}\n"
        bookings_text += "\n"

    await message.answer(
        bookings_text,
        parse_mode="HTML",
        reply_markup=master_kb.get_master_menu_keyboard()
    )


# ===== ПРОСМОТР РАСПИСАНИЯ =====

@router.message(F.text == "📅 Моё расписание")
async def show_schedule_menu(message: Message):
    """Показать меню выбора периода расписания"""
    await message.answer(
        "📅 Выберите период для просмотра расписания:",
        reply_markup=master_kb.get_schedule_filter_keyboard()
    )


@router.callback_query(F.data.startswith("master_schedule:"))
async def show_master_schedule(callback: CallbackQuery):
    """Показать расписание мастера за выбранный период"""
    await callback.answer()

    period = callback.data.split(":")[1]

    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)

        if not employee:
            await callback.message.answer("❌ Вы не зарегистрированы как мастер")
            return

        # Определяем даты для запроса
        today = date.today()

        if period == "today":
            start_date = end_date = today
            period_text = "Сегодня"
        elif period == "tomorrow":
            start_date = end_date = today + timedelta(days=1)
            period_text = "Завтра"
        elif period == "week":
            start_date = today
            end_date = today + timedelta(days=7)
            period_text = "Неделя"
        else:
            start_date = end_date = today
            period_text = "Сегодня"

        # Получаем записи
        bookings = await get_master_bookings(session, employee.id, start_date, end_date)

    if not bookings:
        await callback.message.edit_text(
            f"📅 <b>{period_text}</b>\n\n"
            f"📭 Нет записей на этот период",
            parse_mode="HTML",
            reply_markup=master_kb.get_schedule_filter_keyboard()
        )
        return

    # Группируем записи по датам
    bookings_by_date = {}
    for booking in bookings:
        booking_date = booking.booking_date
        if booking_date not in bookings_by_date:
            bookings_by_date[booking_date] = []
        bookings_by_date[booking_date].append(booking)

    # Формируем текст расписания
    schedule_text = f"📅 <b>{period_text}</b>\n\n"

    for booking_date in sorted(bookings_by_date.keys()):
        # Заголовок даты
        date_str = booking_date.strftime("%d.%m.%Y")
        weekday = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][booking_date.weekday()]
        schedule_text += f"📆 <b>{weekday}, {date_str}</b>\n"

        # Записи на эту дату
        day_bookings = bookings_by_date[booking_date]
        for booking in sorted(day_bookings, key=lambda b: b.booking_time):
            status_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "completed": "✔️",
                "cancelled": "❌"
            }.get(booking.status, "❓")

            time_str = booking.booking_time.strftime("%H:%M")
            client_name = booking.client.full_name
            service_name = booking.service.name

            schedule_text += (
                f"{status_emoji} {time_str} - {client_name}\n"
                f"   └ {service_name}\n"
            )

        schedule_text += "\n"

    # Статистика
    total = len(bookings)
    confirmed = sum(1 for b in bookings if b.status == "confirmed")
    completed = sum(1 for b in bookings if b.status == "completed")

    schedule_text += (
        f"📊 <b>Статистика:</b>\n"
        f"Всего записей: {total}\n"
        f"Подтверждено: {confirmed}\n"
        f"Завершено: {completed}\n"
    )

    # Создаем inline клавиатуру с кликабельными записями
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    # Добавляем кнопки для каждой записи
    for booking_date in sorted(bookings_by_date.keys()):
        day_bookings = bookings_by_date[booking_date]
        for booking in sorted(day_bookings, key=lambda b: b.booking_time):
            status_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "completed": "✔️",
                "cancelled": "❌"
            }.get(booking.status, "❓")

            time_str = booking.booking_time.strftime("%H:%M")
            weekday_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][booking_date.weekday()]
            date_short = f"{booking_date.strftime('%d.%m')} {weekday_short}"

            builder.button(
                text=f"{status_emoji} {date_short} {time_str} - {booking.client.full_name[:12]}",
                callback_data=f"master_booking:{booking.id}"
            )

    # Добавляем кнопки фильтров
    builder.button(text="📅 Сегодня", callback_data="master_schedule:today")
    builder.button(text="📅 Завтра", callback_data="master_schedule:tomorrow")
    builder.button(text="📅 Неделя", callback_data="master_schedule:week")

    builder.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 3)  # Последняя строка - 3 кнопки

    await callback.message.edit_text(
        schedule_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


# ===== ДЕЙСТВИЯ С ЗАПИСЯМИ =====

@router.callback_query(F.data.startswith("master_booking:"))
async def show_booking_details(callback: CallbackQuery):
    """Показать детали записи"""
    await callback.answer()

    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись через get_master_bookings для проверки доступа
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)
        if not employee:
            await callback.message.answer("❌ Вы не зарегистрированы как мастер")
            return

        # Получаем записи мастера на месяц (для поиска нужной)
        today = date.today()
        bookings = await get_master_bookings(
            session,
            employee.id,
            today - timedelta(days=30),
            today + timedelta(days=30)
        )

        booking = next((b for b in bookings if b.id == booking_id), None)

    if not booking:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="master_main_menu")
        builder.adjust(1)

        await callback.message.answer(
            "❌ Запись не найдена",
            reply_markup=builder.as_markup()
        )
        return

    # Формируем детальную информацию
    status_emoji = {
        "pending": "⏳",
        "confirmed": "✅",
        "completed": "✔️",
        "cancelled": "❌"
    }.get(booking.status, "❓")

    status_text = {
        "pending": "Ожидает подтверждения",
        "confirmed": "Подтверждена",
        "completed": "Завершена",
        "cancelled": "Отменена"
    }.get(booking.status, "Неизвестно")

    date_str = format_date_with_weekday(booking.booking_date)
    time_str = booking.booking_time.strftime("%H:%M")
    phone_str = format_phone(booking.client.phone) if booking.client.phone else 'Не указан'

    details_text = (
        f"{status_emoji} <b>Запись #{booking.id}</b>\n\n"
        f"👤 <b>Клиент:</b> {booking.client.full_name}\n"
        f"📞 <b>Телефон:</b> {phone_str}\n\n"
        f"💇 <b>Услуга:</b> {booking.service.name}\n"
        f"⏱ <b>Длительность:</b> {booking.service.duration_minutes} мин\n"
        f"💰 <b>Стоимость:</b> {booking.service.price}₽\n\n"
        f"📅 <b>Дата:</b> {date_str}\n"
        f"🕐 <b>Время:</b> {time_str}\n\n"
        f"📊 <b>Статус:</b> {status_text}"
    )

    await callback.message.edit_text(
        details_text,
        parse_mode="HTML",
        reply_markup=master_kb.get_master_booking_actions_keyboard(booking_id)
    )


@router.callback_query(F.data.startswith("master_complete:"))
async def complete_booking(callback: CallbackQuery):
    """Завершить запись"""
    await callback.answer()

    try:
        booking_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.answer("❌ Ошибка данных")
        return

    async with async_session_maker() as session:
        try:
            # Проверка принадлежности записи мастеру
            employee = await get_employee_by_telegram_id(session, callback.from_user.id)
            if not employee:
                await callback.message.answer("❌ Вы не зарегистрированы как мастер")
                return

            from sqlalchemy import select as sa_select
            from sqlalchemy.orm import selectinload
            from database.models import Booking as BookingModel
            result = await session.execute(
                sa_select(BookingModel)
                .options(selectinload(BookingModel.client), selectinload(BookingModel.service))
                .where(BookingModel.id == booking_id)
            )
            booking = result.scalar_one_or_none()
            if not booking or booking.employee_id != employee.id:
                await callback.message.answer("❌ Запись не найдена или нет доступа")
                return

            # Раньше здесь можно было «завершить» даже отменённую запись; теперь общие правила:
            # подтверждённая (в т.ч. с подтверждённой предоплатой) завершается целиком, время
            # мастера освобождается с этого момента.
            from database.booking_changes import BookingChangeError, finish_visit_now
            try:
                await finish_visit_now(session, booking)
            except BookingChangeError as exc:
                await callback.message.answer(f"❌ {exc.detail}")
                return
            await session.commit()


            await callback.message.edit_text(
                f"✔️ <b>Запись #{booking_id} завершена!</b>\n\n"
                f"👤 Клиент: {booking.client.full_name}\n"
                f"💇 Услуга: {booking.service.name}",
                parse_mode="HTML"
            )
        except Exception as e:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Главное меню", callback_data="master_main_menu")
            builder.adjust(1)

            await callback.message.answer(
                f"❌ Ошибка: {str(e)}",
                reply_markup=builder.as_markup()
            )


@router.callback_query(F.data.startswith("master_chsvc:"))
async def master_change_service_menu(callback: CallbackQuery):
    """Показать услуги бизнеса, на которые можно заменить услугу в записи"""
    await callback.answer()
    try:
        booking_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.answer("❌ Ошибка данных")
        return

    from sqlalchemy import select as sa_select
    from database.models import Booking as BookingModel, Service as ServiceModel
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)
        booking = await session.get(BookingModel, booking_id) if employee else None
        if not employee or not booking or booking.employee_id != employee.id:
            await callback.message.answer("❌ Запись не найдена или нет доступа")
            return
        current = await session.get(ServiceModel, booking.service_id)
        services = (await session.execute(
            sa_select(ServiceModel).where(
                ServiceModel.business_id == booking.business_id,
                ServiceModel.is_active.is_(True),
                ServiceModel.id != booking.service_id,
                ServiceModel.is_additional.is_(bool(current and current.is_additional)),
            ).order_by(ServiceModel.sort_order, ServiceModel.id)
        )).scalars().all()
    builder = InlineKeyboardBuilder()
    for sv in services:
        builder.button(text=f"{sv.name} · {sv.duration_minutes} мин", callback_data=f"master_setsvc:{booking_id}:{sv.id}")
    builder.button(text="◀️ Назад", callback_data=f"master_booking:{booking_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        "🔁 <b>На какую услугу заменить?</b>\nИспользуйте, если клиент выбрал не ту.",
        parse_mode="HTML", reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("master_setsvc:"))
async def master_change_service_apply(callback: CallbackQuery):
    """Заменить услугу в записи"""
    await callback.answer()
    try:
        _, booking_id, service_id = callback.data.split(":")
        booking_id, service_id = int(booking_id), int(service_id)
    except ValueError:
        await callback.message.answer("❌ Ошибка данных")
        return

    from sqlalchemy.orm import selectinload
    from database.models import Booking as BookingModel
    from database.booking_changes import BookingChangeError, change_booking_service
    from sqlalchemy import select as sa_select
    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)
        booking = None
        if employee:
            booking = (await session.execute(
                sa_select(BookingModel).options(selectinload(BookingModel.service))
                .where(BookingModel.id == booking_id, BookingModel.employee_id == employee.id)
                .with_for_update()
            )).scalar_one_or_none()
        if not booking:
            await callback.message.answer("❌ Запись не найдена или нет доступа")
            return
        try:
            await change_booking_service(session, booking, service_id)
            await session.commit()
        except BookingChangeError as exc:
            await session.rollback()
            await callback.message.answer(f"❌ {exc.detail}")
            return
        name = booking.service.name
    await callback.message.answer(f"✅ Услуга в записи #{booking_id} заменена на «{name}».")
    await show_booking_details(callback)


@router.callback_query(F.data.startswith("master_cancel:"))
async def cancel_booking(callback: CallbackQuery):
    """Отменить запись"""
    await callback.answer()

    try:
        booking_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.message.answer("❌ Ошибка данных")
        return

    async with async_session_maker() as session:
        try:
            employee = await get_employee_by_telegram_id(session, callback.from_user.id)
            if not employee:
                await callback.message.answer("❌ Вы не зарегистрированы как мастер")
                return

            from sqlalchemy import select as sa_select
            from sqlalchemy.orm import selectinload
            from database.models import Booking as BookingModel
            result = await session.execute(
                sa_select(BookingModel)
                .options(selectinload(BookingModel.client), selectinload(BookingModel.service))
                .where(BookingModel.id == booking_id)
            )
            booking = result.scalar_one_or_none()
            if not booking or booking.employee_id != employee.id:
                await callback.message.answer("❌ Запись не найдена или нет доступа")
                return

            booking.status = "cancelled"
            await session.commit()


            await callback.message.edit_text(
                f"❌ <b>Запись #{booking_id} отменена</b>\n\n"
                f"👤 Клиент: {booking.client.full_name}\n"
                f"💇 Услуга: {booking.service.name}",
                parse_mode="HTML"
            )
        except Exception as e:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Главное меню", callback_data="master_main_menu")
            builder.adjust(1)

            await callback.message.answer(
                f"❌ Ошибка: {str(e)}",
                reply_markup=builder.as_markup()
            )


@router.callback_query(F.data.startswith("master_client_history:"))
async def show_client_history_from_booking(callback: CallbackQuery):
    """Показать историю клиента из записи"""
    await callback.answer()

    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)
        if not employee:
            await callback.message.answer("❌ Вы не зарегистрированы как мастер")
            return

        today = date.today()
        bookings = await get_master_bookings(
            session,
            employee.id,
            today - timedelta(days=365),  # История за год
            today + timedelta(days=30)
        )

        booking = next((b for b in bookings if b.id == booking_id), None)

        if not booking:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Главное меню", callback_data="master_main_menu")
            builder.adjust(1)

            await callback.message.answer(
                "❌ Запись не найдена",
                reply_markup=builder.as_markup()
            )
            return

        # Получаем всю историю клиента в этом бизнесе
        client_id = booking.client_id
        business_id = employee.business_id

        # Получаем все записи клиента в бизнесе
        all_business_bookings = await get_business_bookings(session, business_id)
        client_bookings = [b for b in all_business_bookings if b.client_id == client_id]

    # Формируем текст истории
    phone_str = format_phone(booking.client.phone) if booking.client.phone else 'Не указан'
    history_text = f"👤 <b>История клиента</b>\n\n"
    history_text += f"Имя: {booking.client.full_name}\n"
    history_text += f"📞 Телефон: {phone_str}\n\n"

    # Статистика
    total_bookings = len(client_bookings)
    completed = sum(1 for b in client_bookings if b.status == "completed")
    cancelled = sum(1 for b in client_bookings if b.status == "cancelled")
    total_spent = sum(b.service.price or 0 for b in client_bookings if b.status == "completed")

    history_text += f"📊 <b>Статистика:</b>\n"
    history_text += f"Всего визитов: {total_bookings}\n"
    history_text += f"Завершено: {completed}\n"
    history_text += f"Отменено: {cancelled}\n"
    history_text += f"Потрачено: {total_spent:,.0f}₽\n\n"

    # Последние 5 посещений
    recent_bookings = sorted(
        client_bookings,
        key=lambda b: (b.booking_date, b.booking_time),
        reverse=True
    )[:5]

    if recent_bookings:
        history_text += f"📋 <b>Последние визиты:</b>\n"
        for b in recent_bookings:
            status_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "completed": "✔️",
                "cancelled": "❌"
            }.get(b.status, "❓")

            date_str = format_date_with_weekday(b.booking_date)
            history_text += f"{status_emoji} {date_str} - {b.service.name}\n"

    await callback.message.edit_text(
        history_text,
        parse_mode="HTML",
        reply_markup=master_kb.get_master_booking_actions_keyboard(booking_id)
    )


# ===== СТАТИСТИКА МАСТЕРА =====

@router.message(F.text == "📊 Моя статистика")
async def show_master_statistics(message: Message):
    """Показать статистику работы мастера"""
    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

        if not employee:
            await message.answer("❌ Вы не зарегистрированы как мастер")
            return

        # Получаем записи за разные периоды
        today = date.today()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Сегодня
        today_bookings = await get_master_bookings(session, employee.id, today, today)

        # Неделя
        week_bookings = await get_master_bookings(session, employee.id, week_ago, today)

        # Месяц
        month_bookings = await get_master_bookings(session, employee.id, month_ago, today)

    # Подсчет статистики
    today_total = len(today_bookings)
    today_completed = sum(1 for b in today_bookings if b.status == "completed")
    today_revenue = sum(b.service.price or 0 for b in today_bookings if b.status == "completed")

    week_total = len(week_bookings)
    week_completed = sum(1 for b in week_bookings if b.status == "completed")
    week_revenue = sum(b.service.price or 0 for b in week_bookings if b.status == "completed")

    month_total = len(month_bookings)
    month_completed = sum(1 for b in month_bookings if b.status == "completed")
    month_revenue = sum(b.service.price or 0 for b in month_bookings if b.status == "completed")

    stats_text = (
        f"📊 <b>Статистика работы</b>\n"
        f"👤 Мастер: {employee.name}\n\n"
        f"📅 <b>Сегодня:</b>\n"
        f"├ Всего записей: {today_total}\n"
        f"├ Завершено: {today_completed}\n"
        f"└ Выручка: {today_revenue:,.0f}₽\n\n"
        f"📅 <b>За неделю:</b>\n"
        f"├ Всего записей: {week_total}\n"
        f"├ Завершено: {week_completed}\n"
        f"└ Выручка: {week_revenue:,.0f}₽\n\n"
        f"📅 <b>За месяц:</b>\n"
        f"├ Всего записей: {month_total}\n"
        f"├ Завершено: {month_completed}\n"
        f"└ Выручка: {month_revenue:,.0f}₽"
    )

    await message.answer(stats_text, parse_mode="HTML")
