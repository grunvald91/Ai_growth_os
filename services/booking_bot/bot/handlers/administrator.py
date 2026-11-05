"""
Обработчики для панели администратора бизнеса (role='administrator' в Employee)
Администратор может записывать клиентов к любому мастеру, отменять записи,
видеть информацию о клиентах и статистику.
"""
from datetime import date, datetime, timedelta
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import BaseFilter

from database import async_session_maker
from database.crud import (
    get_administrator_by_telegram_id,
    get_business_employees,
    get_business_services,
    get_business_bookings,
    get_or_create_guest_user,
    create_booking,
    update_booking_status,
    get_booking_by_id,
    get_user_businesses,
    get_employee_by_id,
    add_employee_day_off,
    remove_employee_day_off,
    get_employee_days_off,
    get_employee_service_ids,
    set_employee_services,
)
from bot.utils.recurring import compute_recurring_dates, build_visit_slots
from bot.keyboards import administrator_kb
from bot.keyboards.client_kb import get_services_keyboard, get_booking_calendar_keyboard, get_times_keyboard
from bot.states.administrator import AdminBookingStates, AdminCancelStates
from bot.config import ADMIN_IDS
from bot.utils.validators import format_date_with_weekday, format_phone

router = Router()


class AdministratorModeFilter(BaseFilter):
    """Фильтр для проверки, что пользователь в режиме администратора"""
    async def __call__(self, message: Message, state: FSMContext) -> bool:
        data = await state.get_data()
        return data.get("current_mode") == "administrator"


async def check_administrator(telegram_id: int, state: FSMContext = None):
    """Проверка, является ли пользователь администратором.

    Если передан state и в нём есть admin_employee_id - используем его.
    Это позволяет работать с конкретным бизнесом, когда админ работает в нескольких.
    """
    async with async_session_maker() as session:
        # Если есть state с сохранённым employee_id - используем его
        if state:
            data = await state.get_data()
            admin_employee_id = data.get("admin_employee_id")
            if admin_employee_id:
                admin = await get_employee_by_id(session, admin_employee_id)
                if admin and admin.telegram_id == telegram_id:
                    return admin

        # Иначе - старая логика (первый найденный)
        admin = await get_administrator_by_telegram_id(session, telegram_id)
        return admin


# ===== ПРОСМОТР ВСЕХ ЗАПИСЕЙ =====

@router.message(F.text == "📋 Все записи")
async def show_all_bookings(message: Message, state: FSMContext):
    """Показать все записи бизнеса"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    await state.update_data(business_id=admin.business_id)

    await message.answer(
        "📋 <b>Записи бизнеса</b>\n\n"
        "Выберите период для просмотра:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_admin_bookings_filter_keyboard()
    )


@router.callback_query(F.data.startswith("admin_filter:"))
async def filter_bookings(callback: CallbackQuery, state: FSMContext):
    """Фильтрация записей по периоду"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    filter_type = callback.data.split(":")[1]
    today = date.today()

    if filter_type == "today":
        start_date = today
        end_date = today
        period_text = "Сегодня"
    elif filter_type == "tomorrow":
        start_date = today + timedelta(days=1)
        end_date = start_date
        period_text = "Завтра"
    elif filter_type == "week":
        start_date = today
        end_date = today + timedelta(days=7)
        period_text = "Ближайшая неделя"
    elif filter_type == "month":
        start_date = today
        end_date = today + timedelta(days=30)
        period_text = "Ближайший месяц"
    else:  # all
        start_date = today - timedelta(days=30)
        end_date = today + timedelta(days=60)
        period_text = "Все записи"

    async with async_session_maker() as session:
        bookings = await get_business_bookings(
            session, admin.business_id, start_date, end_date
        )

    if not bookings:
        await callback.message.edit_text(
            f"📋 <b>{period_text}</b>\n\n"
            "Записей не найдено.",
            parse_mode="HTML",
            reply_markup=administrator_kb.get_admin_bookings_filter_keyboard()
        )
        await callback.answer()
        return

    # Формируем список записей
    bookings_text = ""
    for b in bookings[:15]:  # Лимит 15 записей
        status_emoji = {"pending": "⏳", "confirmed": "✅", "completed": "✔️", "cancelled": "❌"}.get(b.status, "❓")
        client_name = b.client.full_name if b.client else "Без имени"
        master_name = b.employee.name if b.employee else "Любой"
        service_name = b.service.name if b.service else "Услуга"

        bookings_text += (
            f"{status_emoji} <b>{format_date_with_weekday(b.booking_date, short=True)} {b.booking_time.strftime('%H:%M')}</b>\n"
            f"   👤 {client_name}\n"
            f"   💇 {service_name} | 👨‍💼 {master_name}\n\n"
        )

    await callback.message.edit_text(
        f"📋 <b>{period_text}</b>\n"
        f"Найдено записей: {len(bookings)}\n\n"
        f"{bookings_text}",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_admin_bookings_filter_keyboard()
    )
    await callback.answer()


# ===== ЗАПИСЬ КЛИЕНТА =====

@router.message(F.text == "➕ Записать клиента", AdministratorModeFilter())
async def start_admin_booking(message: Message, state: FSMContext):
    """Начать процесс записи клиента"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    # Получаем мастеров бизнеса
    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        # Фильтруем только мастеров (включая совмещенные роли)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    if not masters:
        await message.answer("❌ В бизнесе нет активных мастеров для записи.")
        return

    await state.update_data(business_id=admin.business_id)
    await state.set_state(AdminBookingStates.choosing_master)

    await message.answer(
        "➕ <b>Запись клиента</b>\n\n"
        "Выберите мастера:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_master_selection_keyboard(masters)
    )


@router.callback_query(F.data.startswith("admin_master:"), AdminBookingStates.choosing_master)
async def admin_choose_master(callback: CallbackQuery, state: FSMContext):
    """Выбор мастера для записи"""
    master_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=master_id)

    data = await state.get_data()
    business_id = data.get("business_id")

    # Получаем услуги мастера/бизнеса
    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)
        active_services = [s for s in services if s.is_active]

    if not active_services:
        await callback.message.edit_text("❌ Нет активных услуг.")
        await state.clear()
        await callback.answer()
        return

    await state.set_state(AdminBookingStates.choosing_service)

    await callback.message.edit_text(
        "📋 <b>Выберите услугу:</b>",
        parse_mode="HTML",
        reply_markup=get_services_keyboard(active_services)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("service:"), AdminBookingStates.choosing_service)
async def admin_choose_service(callback: CallbackQuery, state: FSMContext):
    """Выбор услуги"""
    service_id = int(callback.data.split(":")[1])

    data = await state.get_data()
    selected_ids = data.get("selected_service_ids", [])

    if service_id in selected_ids:
        selected_ids.remove(service_id)
    else:
        selected_ids.append(service_id)

    await state.update_data(selected_service_ids=selected_ids)

    # Обновляем клавиатуру
    data = await state.get_data()
    business_id = data.get("business_id")

    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)
        active_services = [s for s in services if s.is_active]

    await callback.message.edit_reply_markup(
        reply_markup=get_services_keyboard(active_services, selected_ids)
    )
    await callback.answer()


@router.callback_query(F.data == "services_done", AdminBookingStates.choosing_service)
async def admin_services_done(callback: CallbackQuery, state: FSMContext):
    """Переход к выбору даты"""
    data = await state.get_data()
    selected_ids = data.get("selected_service_ids", [])

    if not selected_ids:
        await callback.answer("Выберите хотя бы одну услугу", show_alert=True)
        return

    # Получаем информацию о выбранных услугах и считаем общую длительность/стоимость
    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        services_info = []
        total_duration = 0
        total_price = 0
        for service_id in selected_ids:
            service = await get_service_by_id(session, service_id)
            if service:
                services_info.append({
                    "id": service.id,
                    "name": service.name,
                    "duration": service.duration_minutes,
                    "price": service.price or 0
                })
                total_duration += service.duration_minutes
                if service.price:
                    total_price += service.price

    # Сохраняем информацию о услугах
    await state.update_data(
        services_info=services_info,
        total_duration=total_duration,
        total_price=total_price
    )

    await state.set_state(AdminBookingStates.choosing_date)

    # Получаем employee_id из state для проверки выходных
    employee_id = data.get("employee_id")
    employee_name = data.get("employee_name", "Выбранный мастер")

    # Получаем выходные дни мастера
    exclude_dates = []
    if employee_id:
        today = date.today()
        async with async_session_maker() as session:
            days_off = await get_employee_days_off(
                session, employee_id,
                start_date=today,
                end_date=today + timedelta(days=60)
            )
            exclude_dates = [d.date for d in days_off]

    # Показываем календарь
    today = date.today()
    await callback.message.edit_text(
        f"👤 Мастер: {employee_name}\n\n"
        f"📅 Выберите дату:\n"
        f"<i>· — прошедшие дни, ✕ — выходной мастера</i>",
        parse_mode="HTML",
        reply_markup=get_booking_calendar_keyboard(
            today.year, today.month,
            exclude_dates=exclude_dates,
            callback_prefix="date",
            back_callback="back_to_employees"
        )
    )
    await callback.answer()


@router.callback_query(F.data == "noop")
async def noop_handler(callback: CallbackQuery):
    """Обработчик для некликабельных кнопок календаря"""
    await callback.answer()


@router.callback_query(F.data.startswith("cal_nav:"), AdminBookingStates.choosing_date)
async def admin_calendar_navigation(callback: CallbackQuery, state: FSMContext):
    """Навигация по календарю"""
    parts = callback.data.split(":")
    year = int(parts[1])
    month = int(parts[2])

    data = await state.get_data()
    employee_id = data.get("employee_id")
    employee_name = data.get("employee_name", "Выбранный мастер")

    # Получаем выходные дни мастера
    exclude_dates = []
    if employee_id:
        today = date.today()
        async with async_session_maker() as session:
            days_off = await get_employee_days_off(
                session, employee_id,
                start_date=today,
                end_date=today + timedelta(days=60)
            )
            exclude_dates = [d.date for d in days_off]

    await callback.message.edit_text(
        f"👤 Мастер: {employee_name}\n\n"
        f"📅 Выберите дату:\n"
        f"<i>· — прошедшие дни, ✕ — выходной мастера</i>",
        parse_mode="HTML",
        reply_markup=get_booking_calendar_keyboard(
            year, month,
            exclude_dates=exclude_dates,
            callback_prefix="date",
            back_callback="back_to_employees"
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("date:"), AdminBookingStates.choosing_date)
async def admin_choose_date(callback: CallbackQuery, state: FSMContext):
    """Выбор даты"""
    date_str = callback.data.split(":")[1]
    await state.update_data(booking_date=date_str)
    await state.set_state(AdminBookingStates.choosing_time)

    # Генерируем времена (упрощенно - каждые 30 минут с 9:00 до 20:00)
    times = []
    for hour in range(9, 20):
        times.append(f"{hour:02d}:00")
        times.append(f"{hour:02d}:30")

    await callback.message.edit_text(
        f"🕐 <b>Выберите время на {date_str}:</b>",
        parse_mode="HTML",
        reply_markup=get_times_keyboard(times)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("time:"), AdminBookingStates.choosing_time)
async def admin_choose_time(callback: CallbackQuery, state: FSMContext):
    """Выбор времени, переход к вводу данных клиента"""
    time_str = callback.data.split(":", 1)[1]  # "time:11:00" -> "11:00"
    await state.update_data(booking_time=time_str)
    await state.set_state(AdminBookingStates.entering_client_name)

    await callback.message.edit_text(
        "👤 <b>Введите имя клиента:</b>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(AdminBookingStates.entering_client_name)
async def admin_enter_client_name(message: Message, state: FSMContext):
    """Ввод имени клиента"""
    client_name = message.text.strip()
    if len(client_name) < 2:
        await message.answer("❌ Имя слишком короткое. Введите имя клиента:")
        return

    await state.update_data(client_name=client_name)
    await state.set_state(AdminBookingStates.entering_client_phone)

    await message.answer(
        "📱 <b>Введите телефон клиента:</b>\n\n"
        "Формат: +7XXXXXXXXXX или 8XXXXXXXXXX",
        parse_mode="HTML"
    )


@router.message(AdminBookingStates.entering_client_phone)
async def admin_enter_client_phone(message: Message, state: FSMContext):
    """Ввод телефона и переход к выбору повтора записи"""
    phone = message.text.strip()

    # Простая валидация телефона
    phone_clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if not (phone_clean.startswith("+7") or phone_clean.startswith("8")) or len(phone_clean) < 11:
        await message.answer("❌ Неверный формат телефона. Попробуйте еще раз:")
        return

    await state.update_data(client_phone=phone)
    await state.set_state(AdminBookingStates.choosing_repeat)

    await message.answer(
        "Повторять эту запись каждую неделю?",
        reply_markup=administrator_kb.get_admin_repeat_toggle_keyboard()
    )


async def show_admin_confirmation(message: Message, state: FSMContext):
    """Показ экрана подтверждения ручной записи"""
    data = await state.get_data()

    services_info = data.get('services_info', [])
    if services_info:
        services_text = "\n".join([f"  • {s['name']} ({s['duration']} мин)" for s in services_info])
        total_duration = data.get('total_duration', 0)
        total_price = data.get('total_price', 0)
        price_text = f"💰 Общая стоимость: {total_price}₽\n" if total_price > 0 else ""
        services_display = f"💼 Услуги:\n{services_text}\n⏱ Общая длительность: {total_duration} мин\n{price_text}"
    else:
        services_display = ""

    repeat_weeks = data.get('repeat_weeks')
    repeat_line = f"🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю\n" if repeat_weeks and repeat_weeks > 1 else ""

    await message.answer(
        f"✅ <b>Подтверждение записи</b>\n\n"
        f"👤 Клиент: {data.get('client_name')}\n"
        f"📱 Телефон: {data.get('client_phone')}\n"
        f"{services_display}"
        f"📅 Дата: {data.get('booking_date')}\n"
        f"🕐 Время: {data.get('booking_time')}\n"
        f"{repeat_line}\n"
        f"Создать запись?",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_admin_confirmation_keyboard()
    )
    await state.set_state(AdminBookingStates.confirming)


@router.callback_query(F.data == "admin_repeat_skip", AdminBookingStates.choosing_repeat)
async def admin_repeat_skip_chosen(callback: CallbackQuery, state: FSMContext):
    """Владелец отказался от повтора"""
    await state.update_data(repeat_weeks=None)
    await show_admin_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "admin_repeat_enable", AdminBookingStates.choosing_repeat)
async def admin_repeat_enable_chosen(callback: CallbackQuery, state: FSMContext):
    """Владелец включил повтор — показываем степпер"""
    await state.update_data(repeat_weeks_draft=2)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=administrator_kb.get_admin_repeat_stepper_keyboard(2)
    )
    await callback.answer()


@router.callback_query(F.data.in_(["admin_repeat_dec", "admin_repeat_inc"]), AdminBookingStates.choosing_repeat)
async def admin_repeat_stepper_changed(callback: CallbackQuery, state: FSMContext):
    """Изменение числа недель повтора степпером"""
    data = await state.get_data()
    weeks = data.get('repeat_weeks_draft', 2)
    if callback.data == "admin_repeat_dec":
        weeks = max(2, weeks - 1)
    else:
        weeks = min(8, weeks + 1)
    await state.update_data(repeat_weeks_draft=weeks)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=administrator_kb.get_admin_repeat_stepper_keyboard(weeks)
    )
    await callback.answer()


@router.callback_query(F.data == "admin_repeat_noop", AdminBookingStates.choosing_repeat)
async def admin_repeat_noop_pressed(callback: CallbackQuery, state: FSMContext):
    """Нажатие на лейбл степпера — просто гасим спиннер"""
    await callback.answer()


@router.callback_query(F.data == "admin_repeat_cancel", AdminBookingStates.choosing_repeat)
async def admin_repeat_stepper_cancelled(callback: CallbackQuery, state: FSMContext):
    """Возврат от степпера к выбору без повтора / повторять"""
    await callback.message.edit_text(
        "Повторять эту запись каждую неделю?",
        reply_markup=administrator_kb.get_admin_repeat_toggle_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_repeat_done", AdminBookingStates.choosing_repeat)
async def admin_repeat_stepper_done(callback: CallbackQuery, state: FSMContext):
    """Подтверждение числа недель — переходим к подтверждению записи"""
    data = await state.get_data()
    await state.update_data(repeat_weeks=data.get('repeat_weeks_draft', 2))
    await show_admin_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "admin_confirm_action", AdminBookingStates.confirming)
async def admin_confirm_booking(callback: CallbackQuery, state: FSMContext):
    """Создание записи (с поддержкой повтора по неделям)"""
    data = await state.get_data()

    try:
        async with async_session_maker() as session:
            # Создаем или находим клиента (гостевой профиль, привязан к телефону)
            user = await get_or_create_guest_user(
                session,
                full_name=data.get("client_name"),
                phone=data.get("client_phone"),
            )

            # Парсим дату и время
            booking_date = datetime.strptime(data.get("booking_date"), "%d.%m.%Y").date()
            booking_time = datetime.strptime(data.get("booking_time"), "%H:%M").time()
            repeat_weeks = data.get('repeat_weeks') or 1
            occurrence_dates = compute_recurring_dates(booking_date, repeat_weeks)

            # Получаем данные для уведомлений
            from database.crud import get_employee_by_id, get_business_by_id, get_service_by_id, check_series_conflicts
            employee_id = data.get("employee_id")
            employee = await get_employee_by_id(session, employee_id) if employee_id else None
            business = await get_business_by_id(session, data.get("business_id"))

            # Получаем информацию о выбранных услугах
            services_info = data.get('services_info', [])
            if services_info:
                visit_slots = build_visit_slots(services_info, booking_time)
            else:
                # Обратная совместимость - одна услуга (старый формат)
                service_id = data.get("selected_service_ids", [0])[0]
                visit_slots = [(service_id, booking_time)]

            conflicts = await check_series_conflicts(session, employee_id, occurrence_dates, visit_slots)
            if conflicts:
                conflict_text = ", ".join(d.strftime("%d.%m") for d in conflicts)
                await callback.message.edit_text(
                    f"❌ Не удалось создать серию: слот занят на даты: {conflict_text}.\n\n"
                    "Ничего не создано — выберите другое время или уменьшите число повторов."
                )
                await state.clear()
                await callback.answer()
                return

            created_bookings = []
            try:
                for occurrence_date in occurrence_dates:
                    for service_id, start_time in visit_slots:
                        booking = await create_booking(
                            session,
                            business_id=data.get("business_id"),
                            client_id=user.id,
                            service_id=service_id,
                            employee_id=employee_id,
                            booking_date=occurrence_date,
                            booking_time=start_time,
                            status="confirmed"
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
                await callback.message.edit_text(f"❌ Не удалось создать серию: {str(e)}. Ничего не создано.")
                await state.clear()
                await callback.answer()
                return

            # Данные для уведомления и итогового экрана — по первому визиту (первые len(visit_slots) записей)
            first_visit_bookings = created_bookings[:len(visit_slots)]
            if services_info:
                services_text = "\n".join([f"  • {s['name']} ({s['duration']} мин)" for s in services_info])
                total_duration = data.get('total_duration', 0)
                total_price = data.get('total_price', 0)
                price_text = f"💰 Стоимость: {total_price}₽\n" if total_price > 0 else ""
            else:
                service = await get_service_by_id(session, visit_slots[0][0])
                services_text = f"  • {service.name if service else 'Не указана'}"
                total_duration = service.duration_minutes if service else 0
                total_price = service.price if service else 0
                price_text = f"💰 Стоимость: {total_price}₽\n" if total_price else ""
            booking_ids = ", ".join([f"#{b.id}" for b in first_visit_bookings])

        repeat_line = f"🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю\n" if repeat_weeks > 1 else ""

        # Формируем текст уведомления
        notification_text = (
            f"📋 <b>Новая запись!</b>\n\n"
            f"👤 Клиент: {data.get('client_name')}\n"
            f"📱 Телефон: {data.get('client_phone')}\n"
            f"💼 Услуги:\n{services_text}\n"
            f"⏱ Длительность: {total_duration} мин\n"
            f"{price_text}"
            f"📅 Дата: {data.get('booking_date')}\n"
            f"🕐 Время: {data.get('booking_time')}\n"
            f"{repeat_line}"
            f"🏢 Бизнес: {business.name if business else ''}"
        )

        # Отправляем уведомление мастеру
        if employee and employee.telegram_id:
            try:
                await callback.bot.send_message(
                    chat_id=employee.telegram_id,
                    text=notification_text,
                    parse_mode="HTML"
                )
            except Exception:
                pass  # Мастер не запустил бота

        # Отправляем уведомление владельцу бизнеса
        if business and business.owner and business.owner.telegram_id:
            try:
                await callback.bot.send_message(
                    chat_id=business.owner.telegram_id,
                    text=notification_text,
                    parse_mode="HTML"
                )
            except Exception:
                pass  # Владелец не запустил бота

        await callback.message.edit_text(
            f"✅ <b>Запись создана!</b>\n\n"
            f"Номер записи: {booking_ids}\n"
            f"👤 {data.get('client_name')}\n"
            f"📅 {data.get('booking_date')} в {data.get('booking_time')}\n"
            f"⏱ Длительность: {total_duration} мин\n"
            f"{repeat_line}",
            parse_mode="HTML"
        )

    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка создания записи: {str(e)}")

    await state.clear()
    await callback.answer()


@router.callback_query(F.data == "admin_cancel_action")
async def admin_cancel_action(callback: CallbackQuery, state: FSMContext):
    """Отмена текущего действия"""
    await state.clear()
    await callback.message.edit_text("❌ Действие отменено.")
    await callback.answer()


@router.callback_query(F.data == "admin_back_to_menu")
async def admin_back_to_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню администратора"""
    await state.clear()
    await callback.message.delete()
    await callback.answer()


# ===== ОТМЕНА ЗАПИСИ =====

@router.message(F.text == "❌ Отменить запись")
async def start_cancel_booking(message: Message, state: FSMContext):
    """Начать процесс отмены записи"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    # Получаем активные записи
    async with async_session_maker() as session:
        bookings = await get_business_bookings(
            session,
            admin.business_id,
            date.today(),
            date.today() + timedelta(days=30)
        )
        # Только pending и confirmed
        active_bookings = [b for b in bookings if b.status in ["pending", "confirmed"]]

    if not active_bookings:
        await message.answer("📋 Нет активных записей для отмены.")
        return

    await state.set_state(AdminCancelStates.choosing_booking)

    await message.answer(
        "❌ <b>Отмена записи</b>\n\n"
        "Выберите запись для отмены:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_bookings_list_keyboard(active_bookings)
    )


@router.callback_query(F.data.startswith("admin_view_booking:"), AdminCancelStates.choosing_booking)
async def view_booking_for_cancel(callback: CallbackQuery, state: FSMContext):
    """Просмотр записи перед отменой"""
    booking_id = int(callback.data.split(":")[1])
    await state.update_data(booking_id_to_cancel=booking_id)

    async with async_session_maker() as session:
        booking = await get_booking_by_id(session, booking_id)

    if not booking:
        await callback.answer("Запись не найдена", show_alert=True)
        return

    await state.set_state(AdminCancelStates.confirming)

    phone_str = format_phone(booking.client.phone) if booking.client and booking.client.phone else 'Не указан'
    await callback.message.edit_text(
        f"❌ <b>Отмена записи #{booking.id}</b>\n\n"
        f"👤 Клиент: {booking.client.full_name if booking.client else 'Не указан'}\n"
        f"📱 Телефон: {phone_str}\n"
        f"📅 Дата: {format_date_with_weekday(booking.booking_date)}\n"
        f"🕐 Время: {booking.booking_time.strftime('%H:%M')}\n"
        f"💇 Услуга: {booking.service.name if booking.service else 'Не указана'}\n\n"
        f"Подтвердите отмену:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_admin_confirmation_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "admin_confirm_action", AdminCancelStates.confirming)
async def confirm_cancel_booking(callback: CallbackQuery, state: FSMContext):
    """Подтверждение отмены записи"""
    data = await state.get_data()
    booking_id = data.get("booking_id_to_cancel")

    async with async_session_maker() as session:
        await update_booking_status(session, booking_id, "cancelled")

    await callback.message.edit_text(f"✅ Запись #{booking_id} отменена.")
    await state.clear()
    await callback.answer()


# ===== КЛИЕНТЫ =====

@router.message(F.text == "👥 Клиенты")
async def show_clients(message: Message, state: FSMContext):
    """Показать список клиентов бизнеса"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    async with async_session_maker() as session:
        # Получаем записи за последние 90 дней для списка клиентов
        bookings = await get_business_bookings(
            session,
            admin.business_id,
            date.today() - timedelta(days=90),
            date.today() + timedelta(days=30)
        )

    # Собираем уникальных клиентов
    clients = {}
    for b in bookings:
        if b.client and b.client.id not in clients:
            clients[b.client.id] = {
                "name": b.client.full_name,
                "phone": b.client.phone or "Не указан",
                "bookings_count": 0
            }
        if b.client:
            clients[b.client.id]["bookings_count"] += 1

    if not clients:
        await message.answer("👥 Клиентов пока нет.")
        return

    # Формируем текст
    clients_text = ""
    for client_id, info in list(clients.items())[:20]:
        clients_text += (
            f"👤 <b>{info['name']}</b>\n"
            f"   📱 {info['phone']}\n"
            f"   📋 Записей: {info['bookings_count']}\n\n"
        )

    await message.answer(
        f"👥 <b>Клиенты бизнеса</b>\n"
        f"Всего: {len(clients)}\n\n"
        f"{clients_text}",
        parse_mode="HTML"
    )


# ===== СТАТИСТИКА =====

@router.message(F.text == "📊 Статистика")
async def show_statistics(message: Message, state: FSMContext):
    """Показать статистику бизнеса"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    async with async_session_maker() as session:
        # Записи за месяц
        bookings_month = await get_business_bookings(
            session,
            admin.business_id,
            date.today() - timedelta(days=30),
            date.today()
        )

        # Записи за неделю
        bookings_week = await get_business_bookings(
            session,
            admin.business_id,
            date.today() - timedelta(days=7),
            date.today()
        )

        # Записи на сегодня
        bookings_today = await get_business_bookings(
            session,
            admin.business_id,
            date.today(),
            date.today()
        )

    # Подсчеты
    completed_month = len([b for b in bookings_month if b.status == "completed"])
    cancelled_month = len([b for b in bookings_month if b.status == "cancelled"])
    pending_today = len([b for b in bookings_today if b.status in ["pending", "confirmed"]])

    # Выручка (если есть цены)
    revenue = sum(
        b.service.price or 0
        for b in bookings_month
        if b.status == "completed" and b.service
    )

    await message.answer(
        f"📊 <b>Статистика бизнеса</b>\n\n"
        f"<b>Сегодня:</b>\n"
        f"   📋 Записей: {len(bookings_today)}\n"
        f"   ⏳ Ожидают: {pending_today}\n\n"
        f"<b>За неделю:</b>\n"
        f"   📋 Записей: {len(bookings_week)}\n\n"
        f"<b>За месяц:</b>\n"
        f"   📋 Всего записей: {len(bookings_month)}\n"
        f"   ✅ Завершено: {completed_month}\n"
        f"   ❌ Отменено: {cancelled_month}\n"
        f"   💰 Выручка: {revenue}₽",
        parse_mode="HTML"
    )


# ===== УПРАВЛЕНИЕ ВЫХОДНЫМИ МАСТЕРОВ =====

@router.message(F.text.in_({"📅 Выходные мастеров", "📅 Выходные мастера", "📅 Расписание"}))
async def show_days_off_menu(message: Message, state: FSMContext):
    """Показать меню управления выходными мастеров"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        # Фильтруем только мастеров (включая совмещенные роли)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    if not masters:
        await message.answer("❌ В бизнесе нет активных мастеров.")
        return

    await state.update_data(business_id=admin.business_id)

    await message.answer(
        "📅 <b>Управление выходными мастеров</b>\n\n"
        "Выберите мастера для управления его выходными днями:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_masters_for_days_off_keyboard(masters)
    )


@router.callback_query(F.data == "daysoff_back_to_masters")
async def back_to_masters_list(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку мастеров"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    await callback.message.edit_text(
        "📅 <b>Управление выходными мастеров</b>\n\n"
        "Выберите мастера для управления его выходными днями:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_masters_for_days_off_keyboard(masters)
    )
    await callback.answer()


@router.callback_query(F.data == "daysoff_close")
async def close_days_off_menu(callback: CallbackQuery):
    """Закрыть меню выходных"""
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data.startswith("daysoff_master:"))
async def select_master_for_days_off(callback: CallbackQuery, state: FSMContext):
    """Выбор мастера для управления выходными"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        if not employee or employee.business_id != admin.business_id:
            await callback.answer("❌ Мастер не найден", show_alert=True)
            return

        # Получаем выходные на ближайший месяц
        today = date.today()
        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=today,
            end_date=today + timedelta(days=60)
        )

    days_off_text = ""
    if days_off:
        days_off_list = [format_date_with_weekday(d.date) for d in days_off[:10]]
        days_off_text = "\n\n📋 <b>Ближайшие выходные:</b>\n" + "\n".join(f"   • {d}" for d in days_off_list)
        if len(days_off) > 10:
            days_off_text += f"\n   <i>...и ещё {len(days_off) - 10}</i>"

    await callback.message.edit_text(
        f"👤 <b>{employee.name}</b>\n"
        f"💼 {employee.specialization or 'Без специализации'}"
        f"{days_off_text}\n\n"
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_days_off_management_keyboard(employee_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("daysoff_add:"))
async def show_add_day_off_calendar(callback: CallbackQuery, state: FSMContext):
    """Показать календарь для добавления выходного"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])
    today = date.today()

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        # Получаем выходные за текущий месяц
        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=date(today.year, today.month, 1),
            end_date=date(today.year, today.month + 1 if today.month < 12 else 1, 1) - timedelta(days=1)
        )
        days_off_numbers = [d.date.day for d in days_off]

    await callback.message.edit_text(
        f"➕ <b>Добавление выходного для {employee.name}</b>\n\n"
        f"🔴 — уже выходной\n"
        f"Выберите дату:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_days_off_calendar_keyboard(
            employee_id, today.year, today.month, days_off_numbers, mode="add"
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("daysoff_remove:"))
async def show_remove_day_off_calendar(callback: CallbackQuery, state: FSMContext):
    """Показать календарь для удаления выходного"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])
    today = date.today()

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=date(today.year, today.month, 1),
            end_date=date(today.year, today.month + 1 if today.month < 12 else 1, 1) - timedelta(days=1)
        )
        days_off_numbers = [d.date.day for d in days_off]

    if not days_off_numbers:
        await callback.answer("У мастера нет выходных в этом месяце", show_alert=True)
        return

    await callback.message.edit_text(
        f"🗑 <b>Удаление выходного для {employee.name}</b>\n\n"
        f"🔴 — выходной (нажмите чтобы удалить)\n"
        f"Выберите дату для удаления:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_days_off_calendar_keyboard(
            employee_id, today.year, today.month, days_off_numbers, mode="remove"
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("daysoff_cal:"))
async def navigate_days_off_calendar(callback: CallbackQuery, state: FSMContext):
    """Навигация по календарю выходных"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    employee_id = int(parts[1])
    year = int(parts[2])
    month = int(parts[3])
    mode = parts[4]

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)

        # Определяем первый и последний день месяца
        if month == 12:
            next_month_start = date(year + 1, 1, 1)
        else:
            next_month_start = date(year, month + 1, 1)
        month_end = next_month_start - timedelta(days=1)

        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=date(year, month, 1),
            end_date=month_end
        )
        days_off_numbers = [d.date.day for d in days_off]

    if mode == "add":
        title = f"➕ <b>Добавление выходного для {employee.name}</b>"
        hint = "🔴 — уже выходной"
    else:
        title = f"🗑 <b>Удаление выходного для {employee.name}</b>"
        hint = "🔴 — выходной (нажмите чтобы удалить)"

    await callback.message.edit_text(
        f"{title}\n\n{hint}\nВыберите дату:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_days_off_calendar_keyboard(
            employee_id, year, month, days_off_numbers, mode=mode
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("daysoff_set:"))
async def set_day_off(callback: CallbackQuery, state: FSMContext):
    """Установить выходной день"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    employee_id = int(parts[1])
    year = int(parts[2])
    month = int(parts[3])
    day = int(parts[4])

    day_off_date = date(year, month, day)

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        result = await add_employee_day_off(session, employee_id, day_off_date)

        if result:
            await callback.answer(f"✅ Выходной {day_off_date.strftime('%d.%m.%Y')} добавлен", show_alert=True)
        else:
            await callback.answer("Этот день уже является выходным", show_alert=True)

        # Обновляем календарь
        if month == 12:
            next_month_start = date(year + 1, 1, 1)
        else:
            next_month_start = date(year, month + 1, 1)
        month_end = next_month_start - timedelta(days=1)

        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=date(year, month, 1),
            end_date=month_end
        )
        days_off_numbers = [d.date.day for d in days_off]

    await callback.message.edit_reply_markup(
        reply_markup=administrator_kb.get_days_off_calendar_keyboard(
            employee_id, year, month, days_off_numbers, mode="add"
        )
    )


@router.callback_query(F.data.startswith("daysoff_del:"))
async def delete_day_off(callback: CallbackQuery, state: FSMContext):
    """Удалить выходной день"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    parts = callback.data.split(":")
    employee_id = int(parts[1])
    year = int(parts[2])
    month = int(parts[3])
    day = int(parts[4])

    day_off_date = date(year, month, day)

    async with async_session_maker() as session:
        result = await remove_employee_day_off(session, employee_id, day_off_date)

        if result:
            await callback.answer(f"✅ Выходной {day_off_date.strftime('%d.%m.%Y')} удалён", show_alert=True)
        else:
            await callback.answer("Ошибка удаления", show_alert=True)

        # Обновляем календарь
        if month == 12:
            next_month_start = date(year + 1, 1, 1)
        else:
            next_month_start = date(year, month + 1, 1)
        month_end = next_month_start - timedelta(days=1)

        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=date(year, month, 1),
            end_date=month_end
        )
        days_off_numbers = [d.date.day for d in days_off]

    await callback.message.edit_reply_markup(
        reply_markup=administrator_kb.get_days_off_calendar_keyboard(
            employee_id, year, month, days_off_numbers, mode="remove"
        )
    )


@router.callback_query(F.data.startswith("daysoff_list:"))
async def show_days_off_list(callback: CallbackQuery, state: FSMContext):
    """Показать список всех выходных мастера"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ Нет доступа", show_alert=True)
        return

    employee_id = int(callback.data.split(":")[1])
    today = date.today()

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        days_off = await get_employee_days_off(
            session, employee_id,
            start_date=today,
            end_date=today + timedelta(days=365)
        )

    if not days_off:
        await callback.answer("У мастера нет запланированных выходных", show_alert=True)
        return

    days_list = []
    for d in days_off[:30]:
        reason_text = f" — {d.reason}" if d.reason else ""
        days_list.append(f"• {format_date_with_weekday(d.date)}{reason_text}")

    text = f"📋 <b>Выходные дни {employee.name}</b>\n\n" + "\n".join(days_list)
    if len(days_off) > 30:
        text += f"\n\n<i>...и ещё {len(days_off) - 30} дней</i>"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data=f"daysoff_master:{employee_id}")
    builder.adjust(1)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


# ===== УПРАВЛЕНИЕ УСЛУГАМИ МАСТЕРОВ =====

@router.message(F.text.in_({"🛠 Услуги мастеров", "🛠 Услуги мастера", "🛠 Время аренды"}))
async def show_master_services_menu(message: Message, state: FSMContext):
    """Показать меню управления услугами мастеров"""
    admin = await check_administrator(message.from_user.id, state)
    if not admin:
        await message.answer("❌ У вас нет доступа к этой функции.")
        return

    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        # Фильтруем только мастеров (включая совмещенные роли)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    if not masters:
        await message.answer("❌ В бизнесе нет активных мастеров.")
        return

    await state.update_data(business_id=admin.business_id)

    await message.answer(
        "🛠 <b>Управление услугами мастеров</b>\n\n"
        "Выберите мастера, чтобы назначить ему услуги, которые он может выполнять:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_masters_for_services_keyboard(masters)
    )


@router.callback_query(F.data == "emp_services_close")
async def close_services_menu(callback: CallbackQuery, state: FSMContext):
    """Закрыть меню управления услугами"""
    await callback.message.delete()
    await callback.answer()


@router.callback_query(F.data == "emp_services_back_to_masters")
async def back_to_masters_services_list(callback: CallbackQuery, state: FSMContext):
    """Вернуться к списку мастеров для управления услугами"""
    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    await callback.message.edit_text(
        "🛠 <b>Управление услугами мастеров</b>\n\n"
        "Выберите мастера, чтобы назначить ему услуги, которые он может выполнять:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_masters_for_services_keyboard(masters)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("emp_services_master:"))
async def select_master_for_services(callback: CallbackQuery, state: FSMContext):
    """Выбор мастера для управления его услугами"""
    employee_id = int(callback.data.split(":")[1])

    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        if not employee or employee.business_id != admin.business_id:
            await callback.answer("❌ Мастер не найден", show_alert=True)
            return

        services = await get_business_services(session, admin.business_id)
        selected_service_ids = await get_employee_service_ids(session, employee_id)

    if not services:
        await callback.answer("❌ В бизнесе нет услуг. Сначала добавьте услуги.", show_alert=True)
        return

    # Сохраняем в FSM
    await state.update_data(
        editing_employee_id=employee_id,
        selected_service_ids=selected_service_ids
    )

    await callback.message.edit_text(
        f"🛠 <b>Услуги мастера: {employee.name}</b>\n\n"
        "Выберите услуги, которые может выполнять этот мастер:\n"
        "<i>(✅ — назначенные услуги)</i>",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_employee_services_select_keyboard(
            services, selected_service_ids, employee_id
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("emp_svc_toggle:"))
async def toggle_service_for_employee(callback: CallbackQuery, state: FSMContext):
    """Переключить выбор услуги для мастера"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    service_id = int(parts[2])

    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    data = await state.get_data()
    selected_service_ids = data.get("selected_service_ids", [])

    # Переключаем
    if service_id in selected_service_ids:
        selected_service_ids.remove(service_id)
    else:
        selected_service_ids.append(service_id)

    await state.update_data(selected_service_ids=selected_service_ids)

    # Обновляем клавиатуру
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        services = await get_business_services(session, admin.business_id)

    await callback.message.edit_text(
        f"🛠 <b>Услуги мастера: {employee.name}</b>\n\n"
        "Выберите услуги, которые может выполнять этот мастер:\n"
        "<i>(✅ — назначенные услуги)</i>",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_employee_services_select_keyboard(
            services, selected_service_ids, employee_id
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("emp_svc_save:"))
async def save_employee_services_admin(callback: CallbackQuery, state: FSMContext):
    """Сохранить выбранные услуги для мастера"""
    employee_id = int(callback.data.split(":")[1])

    admin = await check_administrator(callback.from_user.id, state)
    if not admin:
        await callback.answer("❌ У вас нет доступа", show_alert=True)
        return

    data = await state.get_data()
    selected_service_ids = data.get("selected_service_ids", [])

    # Сохраняем в базу
    async with async_session_maker() as session:
        await set_employee_services(session, employee_id, selected_service_ids)
        employee = await get_employee_by_id(session, employee_id)

    await callback.answer(f"✅ Сохранено {len(selected_service_ids)} услуг для {employee.name}", show_alert=True)

    # Возвращаемся к списку мастеров
    async with async_session_maker() as session:
        employees = await get_business_employees(session, admin.business_id)
        masters = [e for e in employees if e.role in ("master", "master_and_admin") and e.is_active]

    await state.update_data(selected_service_ids=None, editing_employee_id=None)

    await callback.message.edit_text(
        "🛠 <b>Управление услугами мастеров</b>\n\n"
        "Выберите мастера, чтобы назначить ему услуги, которые он может выполнять:",
        parse_mode="HTML",
        reply_markup=administrator_kb.get_masters_for_services_keyboard(masters)
    )


# Переключение режима теперь в common.py (👔 Режим администратора)
