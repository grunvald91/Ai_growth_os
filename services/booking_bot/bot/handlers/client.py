"""
Обработчики для клиентской части (запись на услуги)
"""
import logging
from datetime import datetime, timedelta, date, time
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

logger = logging.getLogger(__name__)

from database import async_session_maker
from database.models import Employee
from database.crud import (
    get_or_create_user,
    get_all_active_businesses,
    get_business_services,
    get_business_employees,
    create_booking,
    get_user_bookings,
    update_booking_status,
    get_business_by_id,
    get_service_by_id,
    get_user_businesses,
    get_employee_by_telegram_id,
    check_series_conflicts,
    use_subscription_service,
    release_subscription_usage,
    get_subscription_remaining
)
from bot.utils.recurring import compute_recurring_dates
from bot.states import BookingStates
from bot.keyboards import client_kb
from bot.config import ADMIN_IDS

router = Router()


async def get_user_roles(telegram_id: int):
    """Проверка ролей пользователя для кнопок переключения режимов"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=telegram_id,
            username=None,
            full_name=""
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, telegram_id)

    is_admin = telegram_id in ADMIN_IDS
    show_admin = is_admin or len(businesses) > 0
    show_master = employee is not None

    return show_admin, show_master


# ===== НАЧАЛО ПРОЦЕССА ЗАПИСИ =====

@router.message(F.text == "📅 Записаться")
async def start_booking(message: Message, state: FSMContext):
    """Начало процесса записи"""
    async with async_session_maker() as session:
        businesses = await get_all_active_businesses(session)

    if not businesses:
        await message.answer(
            "К сожалению, сейчас нет доступных бизнесов для записи.\n"
            "Попробуйте позже."
        )
        return

    # Получаем роли пользователя для кнопок переключения
    show_admin, show_master = await get_user_roles(message.from_user.id)

    # Показываем Reply клавиатуру с кнопками переключения режимов
    reply_keyboard = client_kb.get_mode_switch_keyboard(show_owner_button=show_admin, show_employee_button=show_master)

    # Отправляем сообщение с inline клавиатурой для выбора и reply клавиатурой для переключения режимов
    await message.answer(
        "📍 Кнопки переключения режимов внизу ⬇️",
        reply_markup=reply_keyboard
    )
    await message.answer(
        "Выберите заведение:",
        reply_markup=client_kb.get_businesses_keyboard(businesses)
    )

    await state.set_state(BookingStates.choosing_business)


# ===== ВЫБОР БИЗНЕСА =====

@router.callback_query(F.data.startswith("business:"))
async def business_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора бизнеса"""
    current_state = await state.get_state()
    logger.info(f"business_chosen called with data: {callback.data}, state: {current_state}")

    # Если состояние не установлено, устанавливаем его
    if current_state is None:
        logger.warning("State was None, setting to choosing_business")
        await state.set_state(BookingStates.choosing_business)

    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        services = await get_business_services(session, business_id)

    if not business:
        logger.error(f"Business {business_id} not found!")
        await callback.answer("Заведение не найдено", show_alert=True)
        return

    if not services:
        await callback.message.answer(
            f"В бизнесе '{business.name}' пока нет доступных услуг."
        )
        await callback.answer()
        return

    # Сохраняем выбранный бизнес
    await state.update_data(business_id=business_id, business_name=business.name)

    # Формируем информацию о бизнесе
    import json
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from urllib.parse import quote

    info_text = f"🏢 <b>{business.name}</b>\n\n"

    if business.description:
        info_text += f"📝 {business.description}\n\n"

    # Контакты
    if business.phone:
        clean_phone = ''.join(c for c in business.phone if c.isdigit() or c == '+')
        if not clean_phone.startswith('+'):
            clean_phone = '+7' + clean_phone.lstrip('78')
        info_text += f'📞 Телефон: {clean_phone}\n'

    if business.address:
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        info_text += f'📍 Адрес: <a href="{yandex_maps_url}">{business.address}</a>\n'

    # Часы работы
    if business.working_hours_start and business.working_hours_end:
        info_text += f"⏰ Часы работы: {business.working_hours_start.strftime('%H:%M')} - {business.working_hours_end.strftime('%H:%M')}\n"

    # Время перерыва
    if business.break_start and business.break_end:
        info_text += f"☕ Перерыв: {business.break_start.strftime('%H:%M')} - {business.break_end.strftime('%H:%M')}\n"

    info_text += "\n"

    # Проверяем наличие фото и инструкций
    has_photos = False
    has_directions = False

    if business.photos:
        try:
            photos_data = json.loads(business.photos)
            has_photos = len(photos_data) > 0
        except (ValueError, KeyError):
            pass

    has_directions = business.directions is not None and business.directions.strip() != ""

    # Создаем клавиатуру
    builder = InlineKeyboardBuilder()

    if has_photos:
        builder.button(text="📷 Фото заведения", callback_data=f"booking_view_photos:{business_id}")
    if has_directions:
        builder.button(text="🗺 Как добраться", callback_data=f"booking_view_directions:{business_id}")

    builder.button(text="➡️ Продолжить запись", callback_data=f"continue_booking:{business_id}")
    builder.button(text="◀️ Назад к выбору", callback_data="back_to_businesses")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=builder.as_markup()
    )
    await state.set_state(BookingStates.choosing_business)
    await callback.answer()


# ===== ПРОДОЛЖЕНИЕ ЗАПИСИ ПОСЛЕ ПРОСМОТРА ИНФОРМАЦИИ =====

@router.callback_query(F.data.startswith("continue_booking:"))
async def continue_booking_after_info(callback: CallbackQuery, state: FSMContext):
    """Продолжение записи после просмотра информации о бизнесе"""
    logger.info(f"continue_booking_after_info called with data: {callback.data}")
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        services = await get_business_services(session, business_id)

    await callback.message.edit_text(
        f"Выбрано: {business.name}\n\nВыберите услугу:",
        reply_markup=client_kb.get_services_keyboard(services)
    )
    await state.set_state(BookingStates.choosing_service)
    await callback.answer()


@router.callback_query(F.data.startswith("booking_view_photos:"))
async def booking_view_photos_handler(callback: CallbackQuery, state: FSMContext):
    """Просмотр фото во время записи"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business or not business.photos:
        await callback.answer("Фотографии не найдены", show_alert=True)
        return

    import json
    from aiogram.types import InputMediaPhoto
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    try:
        photos_data = json.loads(business.photos)
        if not photos_data:
            await callback.answer("Фотографии не найдены", show_alert=True)
            return

        # Отправляем фотографии
        media_group = []
        for i, photo_id in enumerate(photos_data[:10]):
            if i == 0:
                media_group.append(InputMediaPhoto(
                    media=photo_id,
                    caption=f"📷 {business.name}"
                ))
            else:
                media_group.append(InputMediaPhoto(media=photo_id))

        await callback.message.answer_media_group(media_group)

        # Кнопки навигации
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ Назад к информации", callback_data=f"business:{business_id}")
        builder.adjust(1)

        await callback.message.answer("Вернуться:", reply_markup=builder.as_markup())
        await callback.answer()

    except Exception as e:
        await callback.answer("Ошибка при загрузке фотографий", show_alert=True)


@router.callback_query(F.data.startswith("booking_view_directions:"))
async def booking_view_directions_handler(callback: CallbackQuery, state: FSMContext):
    """Просмотр инструкций во время записи"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business or not business.directions:
        await callback.answer("Инструкция не найдена", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from urllib.parse import quote

    # Формируем текст с адресом
    address_text = ""
    if business.address:
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        gis_url = f"https://2gis.ru/search/{encoded_address}"
        address_text = f'\n\n📍 <b>Адрес:</b> <a href="{yandex_maps_url}">{business.address}</a>\n\n' \
                      f'🗺 <a href="{yandex_maps_url}">Яндекс.Карты</a> | ' \
                      f'<a href="{gis_url}">2ГИС</a>'

    directions_text = f"🗺 <b>Как добраться</b>\n\n{business.directions}{address_text}"

    # Кнопки навигации
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад к информации", callback_data=f"business:{business_id}")
    builder.adjust(1)

    await callback.message.answer(
        directions_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ===== ВЫБОР УСЛУГИ =====

@router.callback_query(F.data.startswith("service:"))
async def service_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора услуги"""
    service_id = int(callback.data.split(":")[1])

    data = await state.get_data()
    business_id = data["business_id"]

    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        employees = await get_business_employees(session, business_id)

    # Сохраняем выбранную услугу
    await state.update_data(
        service_id=service_id,
        service_name=service.name,
        service_duration=service.duration_minutes,
        service_price=service.price
    )

    await callback.message.edit_text(
        f"Услуга: {service.name}\n"
        f"Длительность: {service.duration_minutes} мин\n"
        f"{'Цена: ' + str(service.price) + '₽' if service.price else ''}\n\n"
        "Выберите специалиста:",
        reply_markup=client_kb.get_employees_keyboard(employees)
    )
    await state.set_state(BookingStates.choosing_employee)
    await callback.answer()


# ===== ВЫБОР СОТРУДНИКА =====

@router.callback_query(F.data.startswith("employee:"))
async def employee_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сотрудника"""
    employee_data = callback.data.split(":")[1]

    if employee_data == "any":
        employee_id = None
        employee_name = "Любой мастер"
    else:
        employee_id = int(employee_data)
        # Получаем имя сотрудника из БД
        async with async_session_maker() as session:
            result = await session.execute(
                select(Employee).where(Employee.id == employee_id)
            )
            employee = result.scalar_one_or_none()
            employee_name = employee.name if employee else "Выбранный мастер"

    await state.update_data(employee_id=employee_id, employee_name=employee_name)

    # Генерируем доступные даты (включая сегодня + следующие 7 дней)
    available_dates = []
    for i in range(0, 8):  # Начинаем с 0 чтобы включить сегодня
        future_date = datetime.now().date() + timedelta(days=i)
        if i == 0:
            date_str = f"Сегодня ({future_date.strftime('%d.%m')})"
        elif i == 1:
            date_str = f"Завтра ({future_date.strftime('%d.%m')})"
        else:
            date_str = future_date.strftime("%d.%m.%Y")
        available_dates.append(date_str)

    await callback.message.edit_text(
        f"Мастер: {employee_name}\n\nВыберите дату:",
        reply_markup=client_kb.get_dates_keyboard(available_dates)
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()


# ===== ВЫБОР ДАТЫ =====

@router.callback_query(F.data.startswith("date:"))
async def date_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора даты"""
    date_str_raw = callback.data.split(":", 1)[1]

    # Парсим дату из разных форматов
    if date_str_raw.startswith("Сегодня"):
        selected_date = datetime.now().date()
        date_str = selected_date.strftime("%d.%m.%Y")
    elif date_str_raw.startswith("Завтра"):
        selected_date = datetime.now().date() + timedelta(days=1)
        date_str = selected_date.strftime("%d.%m.%Y")
    else:
        date_str = date_str_raw
        selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()

    await state.update_data(booking_date=date_str)

    # Получаем данные о бизнесе и сотруднике для расчета времени
    data = await state.get_data()
    business_id = data['business_id']
    employee_id = data.get('employee_id')

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

        # Определяем шаг записи
        interval = business.booking_interval_minutes
        if employee_id:
            result = await session.execute(
                select(Employee).where(Employee.id == employee_id)
            )
            employee = result.scalar_one_or_none()
            if employee and employee.booking_interval_minutes:
                interval = employee.booking_interval_minutes

        # Определяем время работы
        start_time = business.working_hours_start if business.working_hours_start else datetime.strptime("09:00", "%H:%M").time()
        end_time = business.working_hours_end if business.working_hours_end else datetime.strptime("18:00", "%H:%M").time()

    # Генерируем доступное время с учетом шага записи
    available_times = []
    current_time = datetime.combine(selected_date, start_time)
    end_datetime = datetime.combine(selected_date, end_time)

    # Если сегодня, начинаем с текущего времени + 1 час
    if selected_date == datetime.now().date():
        now_plus_hour = datetime.now() + timedelta(hours=1)
        if current_time < now_plus_hour:
            current_time = now_plus_hour
            # Округляем до ближайшего интервала
            minutes = (current_time.minute // interval + 1) * interval
            if minutes >= 60:
                current_time = current_time.replace(hour=current_time.hour + 1, minute=0)
            else:
                current_time = current_time.replace(minute=minutes)

    while current_time <= end_datetime:
        # Проверяем, не попадает ли время в перерыв
        is_break = False
        if business.break_start and business.break_end:
            if business.break_start <= current_time.time() < business.break_end:
                is_break = True

        if not is_break:
            available_times.append(current_time.strftime("%H:%M"))

        current_time += timedelta(minutes=interval)

    await callback.message.edit_text(
        f"Дата: {date_str}\n\nВыберите время:",
        reply_markup=client_kb.get_times_keyboard(available_times)
    )
    await state.set_state(BookingStates.choosing_time)
    await callback.answer()


# ===== ВЫБОР ВРЕМЕНИ =====

@router.callback_query(F.data.startswith("time:"))
async def time_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора времени"""
    time_str = callback.data.split(":")[1] + ":" + callback.data.split(":")[2]
    await state.update_data(booking_time=time_str)

    # Проверяем, есть ли у пользователя номер телефона
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

    if not user.phone:
        await callback.message.answer(
            "Для завершения записи, пожалуйста, поделитесь номером телефона:",
            reply_markup=client_kb.get_phone_request_keyboard()
        )
        await state.set_state(BookingStates.entering_phone)
    else:
        data = await state.get_data()
        if data.get('client_subscription_id'):
            # Запись по абонементу — без шага "Повторять?"
            await show_confirmation(callback.message, state)
        else:
            await ask_repeat(callback.message, state)

    await callback.answer()


# ===== ВВОД ТЕЛЕФОНА =====

@router.message(F.contact, BookingStates.entering_phone)
async def phone_received(message: Message, state: FSMContext):
    """Получение номера телефона"""
    phone = message.contact.phone_number

    # Сохраняем телефон в БД
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        from database.crud import update_user_phone
        await update_user_phone(session, user.id, phone)

    data = await state.get_data()
    if data.get('client_subscription_id'):
        await show_confirmation(message, state)
    else:
        await ask_repeat(message, state)


async def ask_repeat(message: Message, state: FSMContext):
    """Показ экрана выбора повтора записи"""
    await message.answer(
        "Повторять эту запись каждую неделю?",
        reply_markup=client_kb.get_repeat_toggle_keyboard()
    )
    await state.set_state(BookingStates.choosing_repeat)


@router.callback_query(F.data == "repeat_skip", BookingStates.choosing_repeat)
async def repeat_skip_chosen(callback: CallbackQuery, state: FSMContext):
    """Пользователь отказался от повтора"""
    await state.update_data(repeat_weeks=None)
    await show_confirmation(callback.message, state)
    await callback.answer()


@router.callback_query(F.data == "repeat_enable", BookingStates.choosing_repeat)
async def repeat_enable_chosen(callback: CallbackQuery, state: FSMContext):
    """Пользователь включил повтор — показываем степпер"""
    await state.update_data(repeat_weeks_draft=2)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=client_kb.get_repeat_stepper_keyboard(2)
    )
    await callback.answer()


@router.callback_query(F.data.in_(["repeat_dec", "repeat_inc"]), BookingStates.choosing_repeat)
async def repeat_stepper_changed(callback: CallbackQuery, state: FSMContext):
    """Изменение числа недель повтора степпером"""
    data = await state.get_data()
    weeks = data.get('repeat_weeks_draft', 2)
    if callback.data == "repeat_dec":
        weeks = max(2, weeks - 1)
    else:
        weeks = min(8, weeks + 1)
    await state.update_data(repeat_weeks_draft=weeks)
    await callback.message.edit_text(
        "Сколько недель повторять запись?",
        reply_markup=client_kb.get_repeat_stepper_keyboard(weeks)
    )
    await callback.answer()


@router.callback_query(F.data == "repeat_noop", BookingStates.choosing_repeat)
async def repeat_noop_pressed(callback: CallbackQuery, state: FSMContext):
    """Нажатие на лейбл степпера — просто гасим спиннер"""
    await callback.answer()


@router.callback_query(F.data == "repeat_cancel", BookingStates.choosing_repeat)
async def repeat_stepper_cancelled(callback: CallbackQuery, state: FSMContext):
    """Возврат от степпера к выбору без повтора / повторять"""
    await callback.message.edit_text(
        "Повторять эту запись каждую неделю?",
        reply_markup=client_kb.get_repeat_toggle_keyboard()
    )
    await callback.answer()


@router.callback_query(F.data == "repeat_done", BookingStates.choosing_repeat)
async def repeat_stepper_done(callback: CallbackQuery, state: FSMContext):
    """Подтверждение числа недель — переходим к подтверждению записи"""
    data = await state.get_data()
    await state.update_data(repeat_weeks=data.get('repeat_weeks_draft', 2))
    await show_confirmation(callback.message, state)
    await callback.answer()


async def show_confirmation(message: Message, state: FSMContext):
    """Показ экрана подтверждения записи"""
    data = await state.get_data()

    repeat_weeks = data.get('repeat_weeks')
    repeat_line = f"\n🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю\n" if repeat_weeks and repeat_weeks > 1 else ""

    subscription_name = data.get('subscription_name')
    payment_line = f"🎫 Оплата: абонемент «{subscription_name}»" if subscription_name else (
        f"💰 Цена: {data['service_price']}₽" if data.get('service_price') else ""
    )

    confirmation_text = f"""
📋 <b>Подтверждение записи</b>

🏢 Заведение: {data['business_name']}
💼 Услуга: {data['service_name']}
⏱ Длительность: {data['service_duration']} мин
{payment_line}
👤 Мастер: {data['employee_name']}
📅 Дата: {data['booking_date']}
🕐 Время: {data['booking_time']}
{repeat_line}
Подтверждаете запись?
"""

    await message.answer(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=client_kb.get_confirmation_keyboard()
    )
    await state.set_state(BookingStates.confirming)


# ===== ПОДТВЕРЖДЕНИЕ ЗАПИСИ =====

@router.callback_query(F.data == "confirm_booking")
async def booking_confirmed(callback: CallbackQuery, state: FSMContext):
    """Подтверждение и создание записи (с поддержкой повтора по неделям)"""
    data = await state.get_data()

    # Парсим дату и время
    booking_date = datetime.strptime(data['booking_date'], "%d.%m.%Y").date()
    booking_time = datetime.strptime(data['booking_time'], "%H:%M").time()
    repeat_weeks = data.get('repeat_weeks') or 1
    occurrence_dates = compute_recurring_dates(booking_date, repeat_weeks)

    async with async_session_maker() as session:
        # Получаем пользователя и бизнес
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        from database.crud import get_business_by_id
        business = await get_business_by_id(session, data['business_id'])
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

        employee_id = data.get('employee_id')
        service_id = data['service_id']
        client_subscription_id = data.get('client_subscription_id')
        prepayment_enabled = bool(
            getattr(business, 'prepayment_enabled', False)
            and not client_subscription_id
        )
        prepayment_percent = int(getattr(business, 'prepayment_percent', None) or 100)
        service_price = int(data.get('service_price') or 0)
        prepayment_amount = (
            int(service_price * prepayment_percent / 100)
            if prepayment_enabled
            else None
        )
        initial_status = "pending_prepayment" if prepayment_enabled else "confirmed"

        if prepayment_enabled and repeat_weeks > 1:
            await callback.message.edit_text(
                "💳 Для бизнеса с предоплатой повторные записи пока оформляются по одной.\n\n"
                "Так каждая предоплата однозначно относится к конкретному визиту."
            )
            await callback.answer()
            return

        conflicts = await check_series_conflicts(
            session, employee_id, occurrence_dates, [(service_id, booking_time)]
        )
        if conflicts:
            conflict_text = ", ".join(d.strftime("%d.%m") for d in conflicts)
            await callback.message.edit_text(
                f"❌ Не удалось создать серию: слот занят на даты: {conflict_text}.\n\n"
                "Ничего не создано — выберите другое время или уменьшите число повторов.",
                reply_markup=client_kb.get_confirmation_keyboard()
            )
            await callback.answer()
            return

        created_bookings = []
        try:
            for occurrence_date in occurrence_dates:
                booking = await create_booking(
                    session,
                    business_id=data['business_id'],
                    client_id=user.id,
                    service_id=service_id,
                    booking_date=occurrence_date,
                    booking_time=booking_time,
                    employee_id=employee_id,
                    status=initial_status,
                    prepayment_amount=prepayment_amount,
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

            show_admin, show_master = await get_user_roles(callback.from_user.id)
            await callback.message.edit_text(
                f"❌ К сожалению, {str(e)}.\n\nПожалуйста, выберите другое время.",
                reply_markup=None
            )
            await callback.message.answer(
                "Попробуйте снова:",
                reply_markup=client_kb.get_main_menu_keyboard(show_admin_button=show_admin, show_master_button=show_master)
            )
            await state.clear()
            await callback.answer()
            return

        booking = created_bookings[0]

        subscription_line = ""
        if client_subscription_id:
            usage = await use_subscription_service(
                session, client_subscription_id, service_id, booking_id=booking.id
            )
            if usage is None:
                from sqlalchemy import delete as sa_delete
                from database.models import Booking
                await session.execute(
                    sa_delete(Booking).where(Booking.id.in_([b.id for b in created_bookings]))
                )
                await session.commit()
                await callback.message.edit_text(
                    "❌ Не удалось списать визит — в абонементе не осталось "
                    "доступных визитов для этой услуги. Ничего не создано.",
                    reply_markup=None,
                )
                await state.clear()
                await callback.answer()
                return

            booking.subscription_usage_id = usage.id
            await session.commit()

            remaining = await get_subscription_remaining(session, client_subscription_id)
            left = remaining.get(service_id, {}).get('remaining')
            if left is not None:
                subscription_line = f"\n🎫 Списано с абонемента «{data.get('subscription_name', '')}», осталось: {left}\n"

        # Mandatory created-event is sent immediately only when no
        # prepayment is required. The Telegram chat itself shows the client
        # confirmation below, so the dispatcher sends client Web Push but does
        # not duplicate the same Telegram message.
        if not prepayment_enabled:
            try:
                from bot.services.notification_events import dispatch_booking_event
                await dispatch_booking_event(
                    created_bookings,
                    session,
                    "booking_created",
                    include_client=True,
                    staff_audience="all",
                    send_client_telegram=False,
                    send_client_webpush=True,
                )
            except Exception as notification_error:
                logger.error(
                    "Telegram booking-created event failed for bookings %s: %s",
                    [item.id for item in created_bookings],
                    notification_error,
                    exc_info=True,
                )

    if prepayment_enabled:
        details = []
        if prepayment_amount is not None:
            details.append(f"💰 Предоплата: <b>{prepayment_amount}₽</b>")
        if business.payment_phone:
            details.append(f"📱 Перевод по номеру: <code>{business.payment_phone}</code>")
        if business.payment_details:
            details.append(f"🏦 Реквизиты: {business.payment_details}")

        prepayment_text = (
            "⏳ <b>Время временно зарезервировано</b>\n\n"
            f"🏢 {business.name}\n"
            f"📅 {data['booking_date']} в {data['booking_time']}\n"
            f"💼 {data['service_name']}\n\n"
            + "\n".join(details)
            + "\n\nПосле перевода подтвердите оплату кнопкой ниже. "
              "Запись станет подтверждённой после проверки владельцем."
        )
        await callback.message.edit_text(
            prepayment_text,
            parse_mode="HTML",
            reply_markup=client_kb.get_prepayment_confirmation_keyboard(
                booking.id,
                require_receipt=bool(getattr(business, 'require_receipt_for_prepayment', False)),
            ),
        )
        await state.update_data(prepayment_booking_id=booking.id)
        await state.set_state(BookingStates.waiting_prepayment)
        await callback.answer("Ожидаем предоплату")
        return

    # Формируем кликабельные ссылки
    phone_text = ""
    if business.phone:
        clean_phone = ''.join(c for c in business.phone if c.isdigit() or c == '+')
        if not clean_phone.startswith('+'):
            clean_phone = '+7' + clean_phone.lstrip('78')
        phone_text = f'\n📞 Телефон: <a href="tel:{clean_phone}">{business.phone}</a>'

    address_text = ""
    if business.address:
        from urllib.parse import quote
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        address_text = f'\n📍 Адрес: <a href="{yandex_maps_url}">{business.address}</a>'

    # Определяем текст о напоминании в зависимости от времени до записи
    booking_datetime = datetime.combine(booking_date, booking_time)
    now = datetime.now()
    time_until_booking = booking_datetime - now

    if time_until_booking.total_seconds() < 24 * 3600:  # Меньше 24 часов
        reminder_text = "Мы отправим вам напоминание за 2 часа до визита."
    else:
        reminder_text = "Мы отправим вам напоминание за день и за 2 часа до визита."

    repeat_note = f"\n🔁 Повторится ещё {repeat_weeks - 1} раз(а) — каждую неделю\n" if repeat_weeks > 1 else ""

    confirmation_text = f"""
✅ <b>Запись успешно создана!</b>

📅 {data['booking_date']} в {data['booking_time']}
🏢 {data['business_name']}
💼 {data['service_name']}
{address_text}
{phone_text}
{repeat_note}
{subscription_line}
{reminder_text}
"""

    # Проверяем наличие фото и инструкций для кнопок
    import json
    has_photos = False
    has_directions = False

    if business.photos:
        try:
            photos_data = json.loads(business.photos)
            has_photos = len(photos_data) > 0
        except (ValueError, KeyError):
            pass

    has_directions = business.directions is not None and business.directions.strip() != ""

    # Создаем клавиатуру с кнопками просмотра информации
    info_keyboard = None
    if has_photos or has_directions:
        info_keyboard = client_kb.get_business_info_keyboard(
            business_id=business.id,
            has_photos=has_photos,
            has_directions=has_directions
        )

    await callback.message.edit_text(
        confirmation_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=info_keyboard
    )

    # Отправляем клиентское меню
    show_admin, show_master = await get_user_roles(callback.from_user.id)
    await callback.message.answer(
        "Что хотите сделать дальше?",
        reply_markup=client_kb.get_main_menu_keyboard(show_admin_button=show_admin, show_master_button=show_master)
    )

    await state.clear()
    await callback.answer("Запись создана!")


@router.callback_query(F.data.startswith("prepay_sent:"))
async def client_prepayment_sent(callback: CallbackQuery, state: FSMContext):
    """Client says the prepayment was sent; owners must confirm receipt."""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        from sqlalchemy.orm import selectinload
        from database.models import Booking

        booking = (
            await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.client),
                    selectinload(Booking.business),
                    selectinload(Booking.service),
                    selectinload(Booking.employee),
                )
                .where(Booking.id == booking_id)
            )
        ).scalar_one_or_none()

        if not booking:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        if not booking.client or booking.client.telegram_id != callback.from_user.id:
            await callback.answer("Это не ваша запись", show_alert=True)
            return
        from database.prepayment import submit_payment, PaymentWindowError
        try:
            visit = await submit_payment(session, booking)
            if not visit:
                await callback.answer("Владелец уже получил запрос на подтверждение")
                return
        except PaymentWindowError as exc:
            await callback.answer(exc.detail, show_alert=True)
            return
        await session.commit()

        try:
            from bot.services.notification_events import dispatch_booking_event
            await dispatch_booking_event(
                [booking],
                session,
                "prepayment_submitted",
                include_client=False,
                staff_audience="owner",
                prepayment_actions=True,
            )
        except Exception as notification_error:
            logger.error(
                "Telegram prepayment notification failed booking=%s: %s",
                booking.id,
                notification_error,
                exc_info=True,
            )

    await callback.message.edit_text(
        callback.message.text
        + "\n\n✅ <b>Сообщение об оплате отправлено владельцу.</b>\n"
          "Запись будет подтверждена после проверки поступления.",
        parse_mode="HTML",
    )
    await state.set_state(BookingStates.waiting_prepayment)
    await callback.answer("Ожидаем подтверждение владельца")


@router.callback_query(F.data.startswith("prepay_attach_receipt:"))
async def client_prepayment_attach_receipt(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":")[1])
    await state.update_data(prepayment_booking_id=booking_id)
    await state.set_state(BookingStates.uploading_receipt)
    await callback.message.answer(
        "📸 Отправьте фото чека или файл/PDF одним сообщением."
    )
    await callback.answer()


@router.message(BookingStates.uploading_receipt)
async def client_prepayment_receipt_uploaded(message: Message, state: FSMContext):
    """Copy the receipt to all owners and request payment confirmation."""
    data = await state.get_data()
    booking_id = data.get("prepayment_booking_id")
    if not booking_id:
        await message.answer("Не удалось определить запись. Откройте её заново.")
        await state.clear()
        return
    if not message.photo and not message.document:
        await message.answer("Пришлите фото чека или документ/PDF.")
        return

    async with async_session_maker() as session:
        from sqlalchemy.orm import selectinload
        from database.models import Booking
        from database.crud import get_business_owners

        booking = (
            await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.client),
                    selectinload(Booking.business),
                    selectinload(Booking.service),
                    selectinload(Booking.employee),
                )
                .where(Booking.id == int(booking_id))
            )
        ).scalar_one_or_none()
        if not booking:
            await message.answer("Запись не найдена.")
            await state.clear()
            return
        if not booking.client or booking.client.telegram_id != message.from_user.id:
            await message.answer("Это не ваша запись.")
            await state.clear()
            return
        from database.prepayment import submit_payment, PaymentWindowError
        from database.receipts import save_receipt, MAX_RECEIPT_BYTES
        import io
        attachment = message.photo[-1] if message.photo else message.document
        if attachment.file_size and attachment.file_size > MAX_RECEIPT_BYTES:
            await message.answer("Чек должен быть не больше 10 МБ")
            return
        try:
            buffer = io.BytesIO()
            await message.bot.download(attachment.file_id, destination=buffer)
            content = buffer.getvalue()
            visit = await submit_payment(session, booking, receipt=True)
            if not visit:
                await message.answer("Чек уже отправлен владельцу")
                await state.set_state(BookingStates.waiting_prepayment)
                return
            save_receipt(session, booking, content)
        except (PaymentWindowError, ValueError) as exc:
            await message.answer(str(exc))
            return
        except Exception:
            logger.exception("Could not persist receipt for booking %s", booking.id)
            await message.answer("Не удалось сохранить чек. Попробуйте ещё раз")
            return
        await session.commit()

        owners = await get_business_owners(session, booking.business_id)
        for owner in owners:
            if not owner.telegram_id or int(owner.telegram_id) <= 0:
                continue
            try:
                await message.bot.copy_message(
                    chat_id=owner.telegram_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
            except Exception as copy_error:
                logger.warning(
                    "Receipt copy failed booking=%s owner=%s: %s",
                    booking.id,
                    owner.id,
                    copy_error,
                )

        try:
            from bot.services.notification_events import dispatch_booking_event
            await dispatch_booking_event(
                [booking],
                session,
                "prepayment_submitted",
                include_client=False,
                staff_audience="owner",
                prepayment_actions=True,
            )
        except Exception as notification_error:
            logger.error(
                "Receipt confirmation request failed booking=%s: %s",
                booking.id,
                notification_error,
                exc_info=True,
            )

    await message.answer(
        "✅ Чек отправлен владельцу. Запись будет подтверждена после проверки."
    )
    await state.set_state(BookingStates.waiting_prepayment)


@router.callback_query(F.data.startswith("cancel_prepay_booking:"))
async def client_cancel_prepayment_booking(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        from sqlalchemy.orm import selectinload
        from database.models import Booking

        booking = (
            await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.client),
                    selectinload(Booking.business),
                    selectinload(Booking.service),
                    selectinload(Booking.employee),
                )
                .where(Booking.id == booking_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not booking:
            await callback.answer("Запись не найдена", show_alert=True)
            return
        if not booking.client or booking.client.telegram_id != callback.from_user.id:
            await callback.answer("Это не ваша запись", show_alert=True)
            return
        if booking.status not in ("pending_prepayment", "prepayment_sent"):
            await callback.answer("Эта запись уже обработана", show_alert=True)
            return

        booking.status = "cancelled"
        await session.commit()

        try:
            from bot.services.notification_events import dispatch_booking_event
            await dispatch_booking_event(
                [booking],
                session,
                "booking_cancelled",
                include_client=True,
                staff_audience="all",
                send_client_telegram=False,
                send_client_webpush=True,
            )
        except Exception as notification_error:
            logger.error(
                "Prepayment cancellation notification failed booking=%s: %s",
                booking.id,
                notification_error,
                exc_info=True,
            )

    await callback.message.edit_text("❌ Бронирование отменено.")
    await state.clear()
    await callback.answer("Запись отменена")


@router.callback_query(F.data == "cancel_booking")
async def booking_cancelled(callback: CallbackQuery, state: FSMContext):
    """Отмена создания записи"""
    show_admin, show_master = await get_user_roles(callback.from_user.id)
    await callback.message.edit_text("Запись отменена.")
    await callback.message.answer(
        "Что хотите сделать?",
        reply_markup=client_kb.get_main_menu_keyboard(show_admin_button=show_admin, show_master_button=show_master)
    )
    await state.clear()
    await callback.answer()


# ===== ПРОСМОТР ЗАПИСЕЙ =====

@router.message(F.text == "📋 Мои записи")
async def show_my_bookings(message: Message):
    """Показать записи пользователя"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        bookings = await get_user_bookings(session, user.id)

    # Получаем роли пользователя для кнопок переключения
    show_admin, show_master = await get_user_roles(message.from_user.id)

    # Показываем Reply клавиатуру с кнопками переключения режимов
    reply_keyboard = client_kb.get_mode_switch_keyboard(show_owner_button=show_admin, show_employee_button=show_master)

    if not bookings:
        await message.answer("У вас пока нет записей.", reply_markup=reply_keyboard)
        return

    # Группируем записи по статусу и времени
    from datetime import datetime
    now = datetime.now()

    active_bookings = []
    for b in bookings:
        if b.status in ["pending", "confirmed"]:
            # Создаём datetime из даты и времени записи
            booking_datetime = datetime.combine(b.booking_date, b.booking_time)
            # Добавляем только будущие записи
            if booking_datetime > now:
                active_bookings.append(b)

    past_bookings = [b for b in bookings if b.status in ["completed", "cancelled"]]

    if active_bookings:
        text = "📅 <b>Активные записи:</b>\n\n"
        for booking in active_bookings:
            status_emoji = "⏳" if booking.status == "pending" else "✅"
            text += f"{status_emoji} <b>{booking.booking_date.strftime('%d.%m.%Y')} в {booking.booking_time.strftime('%H:%M')}</b>\n"
            text += f"🏢 {booking.business.name}\n"
            text += f"💼 {booking.service.name}\n"
            if booking.employee:
                text += f"👤 {booking.employee.name}\n"
            text += "\n"

        # Добавляем inline кнопки
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        import json
        builder = InlineKeyboardBuilder()

        # Группируем записи по бизнесам для кнопок просмотра информации
        businesses_with_bookings = {}
        for booking in active_bookings:
            if booking.business_id not in businesses_with_bookings:
                businesses_with_bookings[booking.business_id] = booking.business

        # Добавляем кнопки для просмотра информации о каждом бизнесе
        for business_id, business in businesses_with_bookings.items():
            # Проверяем наличие фото и инструкций
            has_photos = False
            has_directions = False
            has_contacts = business.phone or business.address

            if business.photos:
                try:
                    photos_data = json.loads(business.photos)
                    has_photos = len(photos_data) > 0
                except:
                    pass

            has_directions = business.directions is not None and business.directions.strip() != ""

            # Создаем кнопки для этого бизнеса
            if has_photos or has_directions or has_contacts:
                builder.button(
                    text=f"ℹ️ Информация: {business.name}",
                    callback_data=f"booking_business_info:{business_id}"
                )

        # Добавляем кнопки отмены для каждой записи
        for booking in active_bookings:
            btn_text = f"❌ Отменить запись от {booking.booking_date.strftime('%d.%m')}"
            builder.button(text=btn_text, callback_data=f"cancel_booking:{booking.id}")

        # Добавляем кнопку "Назад"
        builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

        builder.adjust(1)

        # Отправляем сообщение с reply клавиатурой
        await message.answer(
            "📍 Кнопки переключения режимов внизу ⬇️",
            reply_markup=reply_keyboard
        )
        await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await message.answer("У вас нет активных записей.", reply_markup=reply_keyboard)


# ===== НАВИГАЦИЯ НАЗАД =====

@router.callback_query(F.data == "back_to_businesses")
async def back_to_businesses(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору бизнеса"""
    async with async_session_maker() as session:
        businesses = await get_all_active_businesses(session)

    await callback.message.edit_text(
        "Выберите заведение:",
        reply_markup=client_kb.get_businesses_keyboard(businesses)
    )
    await state.set_state(BookingStates.choosing_business)
    await callback.answer()


@router.callback_query(F.data == "back_to_services")
async def back_to_services(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору услуги"""
    data = await state.get_data()
    business_id = data["business_id"]

    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)

    await callback.message.edit_text(
        f"Выберите услугу:",
        reply_markup=client_kb.get_services_keyboard(services)
    )
    await state.set_state(BookingStates.choosing_service)
    await callback.answer()


@router.callback_query(F.data == "back_to_employees")
async def back_to_employees(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору сотрудника"""
    data = await state.get_data()
    business_id = data["business_id"]

    async with async_session_maker() as session:
        employees = await get_business_employees(session, business_id)

    await callback.message.edit_text(
        "Выберите специалиста:",
        reply_markup=client_kb.get_employees_keyboard(employees)
    )
    await state.set_state(BookingStates.choosing_employee)
    await callback.answer()


@router.callback_query(F.data == "back_to_dates")
async def back_to_dates(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору даты"""
    available_dates = []
    for i in range(1, 8):
        future_date = datetime.now().date() + timedelta(days=i)
        date_str = future_date.strftime("%d.%m.%Y")
        available_dates.append(date_str)

    await callback.message.edit_text(
        "Выберите дату:",
        reply_markup=client_kb.get_dates_keyboard(available_dates)
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()


@router.callback_query(F.data == "back_to_times")
async def back_to_times(callback: CallbackQuery, state: FSMContext):
    """Вернуться к выбору времени"""
    data = await state.get_data()
    date_str = data.get("booking_date")
    business_id = data.get("business_id")
    employee_id = data.get("employee_id")

    if not date_str or not business_id:
        await callback.answer("Ошибка: данные сессии потеряны", show_alert=True)
        return

    # Парсим дату
    selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

        if not business:
            await callback.answer("Ошибка: бизнес не найден", show_alert=True)
            return

        # Определяем шаг записи
        interval = business.booking_interval_minutes
        if employee_id:
            result = await session.execute(
                select(Employee).where(Employee.id == employee_id)
            )
            employee = result.scalar_one_or_none()
            if employee and employee.booking_interval_minutes:
                interval = employee.booking_interval_minutes

        # Определяем время работы
        start_time = business.working_hours_start if business.working_hours_start else datetime.strptime("09:00", "%H:%M").time()
        end_time = business.working_hours_end if business.working_hours_end else datetime.strptime("18:00", "%H:%M").time()

    # Генерируем доступное время с учетом шага записи
    available_times = []
    current_time = datetime.combine(selected_date, start_time)
    end_datetime = datetime.combine(selected_date, end_time)

    # Если сегодня, начинаем с текущего времени + 1 час
    if selected_date == datetime.now().date():
        now_plus_hour = datetime.now() + timedelta(hours=1)
        if current_time < now_plus_hour:
            current_time = now_plus_hour
            # Округляем до ближайшего интервала
            minutes = (current_time.minute // interval + 1) * interval
            if minutes >= 60:
                current_time = current_time.replace(hour=current_time.hour + 1, minute=0)
            else:
                current_time = current_time.replace(minute=minutes)

    while current_time <= end_datetime:
        # Проверяем, не попадает ли время в перерыв
        is_break = False
        if business.break_start and business.break_end:
            if business.break_start <= current_time.time() < business.break_end:
                is_break = True

        if not is_break:
            available_times.append(current_time.strftime("%H:%M"))

        current_time += timedelta(minutes=interval)

    if not available_times:
        await callback.message.edit_text(
            "К сожалению, на эту дату нет свободного времени. Выберите другую дату.",
            reply_markup=client_kb.get_dates_keyboard([date_str])
        )
        await state.set_state(BookingStates.choosing_date)
    else:
        await callback.message.edit_text(
            f"Дата: {date_str}\nВыберите удобное время:",
            reply_markup=client_kb.get_times_keyboard(available_times)
        )
        await state.set_state(BookingStates.choosing_time)

    await callback.answer()


# ===== ОТМЕНА ЗАПИСИ =====

@router.callback_query(F.data.startswith("cancel_booking:"))
async def cancel_booking_by_client(callback: CallbackQuery):
    """Отмена записи клиентом"""
    try:
        booking_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return

    async with async_session_maker() as session:
        # Проверяем, принадлежит ли запись этому пользователю
        from database.crud import get_user_bookings
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        user_bookings = await get_user_bookings(session, user.id)
        booking = None
        for b in user_bookings:
            if b.id == booking_id:
                booking = b
                break

        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        if booking.status not in ['pending', 'confirmed']:
            await callback.answer("❌ Эту запись нельзя отменить", show_alert=True)
            return

        # Отменяем запись
        booking.status = 'cancelled'
        if booking.subscription_usage_id:
            await release_subscription_usage(session, booking.subscription_usage_id)
        await session.commit()

        # Уведомляем владельца об отмене
        from bot.services.notifications import notify_owner_cancelled_booking
        await notify_owner_cancelled_booking(callback.bot, booking)

    await callback.message.edit_text(
        f"✅ Запись на {booking.booking_date.strftime('%d.%m.%Y')} в {booking.booking_time.strftime('%H:%M')} отменена.\n\n"
        f"🏢 {booking.business.name}\n"
        f"💼 {booking.service.name}",
        parse_mode="HTML"
    )

    await callback.answer("Запись отменена")


# ===== ПРОСМОТР ИНФОРМАЦИИ О БИЗНЕСЕ ИЗ ЗАПИСЕЙ =====

@router.callback_query(F.data.startswith("booking_business_info:"))
async def show_booking_business_info(callback: CallbackQuery):
    """Показать информацию о бизнесе из записей"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business:
        await callback.answer("Бизнес не найден", show_alert=True)
        return

    import json
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    # Формируем информацию о бизнесе
    info_text = f"ℹ️ <b>Информация о заведении</b>\n\n"
    info_text += f"🏢 <b>{business.name}</b>\n\n"

    if business.description:
        info_text += f"📝 {business.description}\n\n"

    # Добавляем контактную информацию
    if business.phone:
        # Форматируем телефон в международном формате для кликабельности
        clean_phone = ''.join(c for c in business.phone if c.isdigit() or c == '+')
        if not clean_phone.startswith('+'):
            clean_phone = '+7' + clean_phone.lstrip('78')
        # Telegram автоматически делает телефоны кликабельными в формате +7...
        info_text += f'📞 Телефон: {clean_phone}\n'

    if business.address:
        from urllib.parse import quote
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        gis_url = f"https://2gis.ru/search/{encoded_address}"
        info_text += f'📍 Адрес: <a href="{yandex_maps_url}">{business.address}</a>\n'
        info_text += f'\n🗺 <a href="{yandex_maps_url}">Открыть в Яндекс.Картах</a> | '
        info_text += f'<a href="{gis_url}">2ГИС</a>\n'

    # Рабочие часы
    if business.working_hours_start and business.working_hours_end:
        info_text += f"\n⏰ Часы работы: {business.working_hours_start.strftime('%H:%M')} - {business.working_hours_end.strftime('%H:%M')}\n"

    # Время перерыва
    if business.break_start and business.break_end:
        info_text += f"☕ Перерыв: {business.break_start.strftime('%H:%M')} - {business.break_end.strftime('%H:%M')}\n"

    # Создаем кнопки для фото и инструкций
    builder = InlineKeyboardBuilder()

    has_photos = False
    if business.photos:
        try:
            photos_data = json.loads(business.photos)
            has_photos = len(photos_data) > 0
        except (ValueError, KeyError):
            pass

    has_directions = business.directions is not None and business.directions.strip() != ""

    if has_photos:
        builder.button(text="📷 Посмотреть фото", callback_data=f"view_photos:{business_id}")
    if has_directions:
        builder.button(text="🗺 Как добраться", callback_data=f"view_directions:{business_id}")

    builder.button(text="◀️ Назад к записям", callback_data="back_to_bookings")
    builder.adjust(1)

    await callback.message.answer(
        info_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "back_to_bookings")
async def back_to_bookings_handler(callback: CallbackQuery):
    """Возврат к списку записей"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        bookings = await get_user_bookings(session, user.id)

    # Получаем роли пользователя для кнопок переключения
    show_admin, show_master = await get_user_roles(callback.from_user.id)

    # Показываем Reply клавиатуру с кнопками переключения режимов
    reply_keyboard = client_kb.get_mode_switch_keyboard(show_owner_button=show_admin, show_employee_button=show_master)

    if not bookings:
        await callback.message.answer("У вас пока нет записей.", reply_markup=reply_keyboard)
        await callback.answer()
        return

    # Группируем записи по статусу и времени
    from datetime import datetime
    now = datetime.now()

    active_bookings = []
    for b in bookings:
        if b.status in ["pending", "confirmed"]:
            # Создаём datetime из даты и времени записи
            booking_datetime = datetime.combine(b.booking_date, b.booking_time)
            # Добавляем только будущие записи
            if booking_datetime > now:
                active_bookings.append(b)

    if active_bookings:
        text = "📅 <b>Активные записи:</b>\n\n"
        for booking in active_bookings:
            status_emoji = "⏳" if booking.status == "pending" else "✅"
            text += f"{status_emoji} <b>{booking.booking_date.strftime('%d.%m.%Y')} в {booking.booking_time.strftime('%H:%M')}</b>\n"
            text += f"🏢 {booking.business.name}\n"
            text += f"💼 {booking.service.name}\n"
            if booking.employee:
                text += f"👤 {booking.employee.name}\n"
            text += "\n"

        # Добавляем inline кнопки
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        import json
        builder = InlineKeyboardBuilder()

        # Группируем записи по бизнесам для кнопок просмотра информации
        businesses_with_bookings = {}
        for booking in active_bookings:
            if booking.business_id not in businesses_with_bookings:
                businesses_with_bookings[booking.business_id] = booking.business

        # Добавляем кнопки для просмотра информации о каждом бизнесе
        for business_id, business in businesses_with_bookings.items():
            # Проверяем наличие фото и инструкций
            has_photos = False
            has_directions = False
            has_contacts = business.phone or business.address

            if business.photos:
                try:
                    photos_data = json.loads(business.photos)
                    has_photos = len(photos_data) > 0
                except:
                    pass

            has_directions = business.directions is not None and business.directions.strip() != ""

            # Создаем кнопки для этого бизнеса
            if has_photos or has_directions or has_contacts:
                builder.button(
                    text=f"ℹ️ Информация: {business.name}",
                    callback_data=f"booking_business_info:{business_id}"
                )

        # Добавляем кнопки отмены для каждой записи
        for booking in active_bookings:
            btn_text = f"❌ Отменить запись от {booking.booking_date.strftime('%d.%m')}"
            builder.button(text=btn_text, callback_data=f"cancel_booking:{booking.id}")

        # Добавляем кнопку "Назад"
        builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

        builder.adjust(1)

        await callback.message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())
    else:
        await callback.message.answer("У вас нет активных записей.", reply_markup=reply_keyboard)

    await callback.answer()


@router.callback_query(F.data == "back_to_main_menu")
async def back_to_main_menu_handler(callback: CallbackQuery):
    """Возврат в главное меню"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)

    is_admin = callback.from_user.id in ADMIN_IDS
    show_admin = is_admin or len(businesses) > 0
    show_master = employee is not None

    await callback.message.answer(
        "🏠 Главное меню",
        reply_markup=client_kb.get_main_menu_keyboard(show_admin_button=show_admin, show_master_button=show_master)
    )
    await callback.answer()

    # Удаляем предыдущее сообщение если возможно
    try:
        await callback.message.delete()
    except:
        pass


# ===== ПРОСМОТР ФОТО И ИНСТРУКЦИЙ =====

@router.callback_query(F.data.startswith("view_photos:"))
async def view_business_photos(callback: CallbackQuery):
    """Просмотр фотографий бизнеса"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business or not business.photos:
        await callback.answer("Фотографии не найдены", show_alert=True)
        return

    import json
    from aiogram.types import InputMediaPhoto
    from aiogram.utils.keyboard import InlineKeyboardBuilder

    try:
        photos_data = json.loads(business.photos)
        if not photos_data:
            await callback.answer("Фотографии не найдены", show_alert=True)
            return

        # Отправляем фотографии группой (медиагруппа)
        media_group = []
        for i, photo_id in enumerate(photos_data[:10]):  # Максимум 10 фото
            if i == 0:
                media_group.append(InputMediaPhoto(
                    media=photo_id,
                    caption=f"📷 Фотографии: {business.name}"
                ))
            else:
                media_group.append(InputMediaPhoto(media=photo_id))

        await callback.message.answer_media_group(media_group)

        # Добавляем кнопки навигации
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ Назад к информации", callback_data=f"booking_business_info:{business_id}")
        builder.button(text="📋 К моим записям", callback_data="back_to_bookings")
        builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")
        builder.adjust(1)

        await callback.message.answer("Навигация:", reply_markup=builder.as_markup())
        await callback.answer("✅ Фотографии отправлены")

    except Exception as e:
        await callback.answer("Ошибка при загрузке фотографий", show_alert=True)


@router.callback_query(F.data.startswith("view_directions:"))
async def view_business_directions(callback: CallbackQuery):
    """Просмотр инструкции как добраться"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business or not business.directions:
        await callback.answer("Инструкция не найдена", show_alert=True)
        return

    from aiogram.utils.keyboard import InlineKeyboardBuilder

    # Формируем кликабельный адрес
    address_text = ""
    if business.address:
        from urllib.parse import quote
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        gis_url = f"https://2gis.ru/search/{encoded_address}"
        address_text = f'\n\n📍 <b>Адрес:</b> <a href="{yandex_maps_url}">{business.address}</a>\n\n' \
                      f'🗺 <a href="{yandex_maps_url}">Открыть в Яндекс.Картах</a> | ' \
                      f'<a href="{gis_url}">2ГИС</a>'

    directions_text = f"""
🗺 <b>Как добраться</b>

{business.directions}
{address_text}
"""

    # Добавляем кнопки навигации
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад к информации", callback_data=f"booking_business_info:{business_id}")
    builder.button(text="📋 К моим записям", callback_data="back_to_bookings")
    builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")
    builder.adjust(1)

    await callback.message.answer(
        directions_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=builder.as_markup()
    )
    await callback.answer("✅ Инструкция отправлена")


# ===== БЫСТРАЯ ПОВТОРНАЯ ЗАПИСЬ =====

@router.message(F.text == "🔄 Повторить запись")
async def quick_rebooking(message: Message, state: FSMContext):
    """Быстрая повторная запись на основе истории"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        # Получаем последние 5 завершенных записей
        bookings = await get_user_bookings(session, user.id)
        completed_bookings = [b for b in bookings if b.status == "completed"][:5]

    # Получаем роли пользователя для кнопок переключения
    show_admin, show_master = await get_user_roles(message.from_user.id)
    reply_keyboard = client_kb.get_mode_switch_keyboard(show_owner_button=show_admin, show_employee_button=show_master)

    if not completed_bookings:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

        await message.answer(
            "📍 Кнопки переключения режимов внизу ⬇️",
            reply_markup=reply_keyboard
        )
        await message.answer(
            "У вас пока нет завершенных записей.\n"
            "Используйте '📅 Записаться' для первой записи.",
            reply_markup=builder.as_markup()
        )
        return

    # Создаем кнопки для каждой записи
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    for booking in completed_bookings:
        btn_text = f"{booking.business.name} - {booking.service.name}"
        if booking.employee:
            btn_text += f" ({booking.employee.name})"

        builder.button(
            text=btn_text,
            callback_data=f"repeat_booking:{booking.id}"
        )

    # Добавляем кнопку возврата в главное меню
    builder.button(text="🏠 Главное меню", callback_data="back_to_main_menu")

    builder.adjust(1)

    await message.answer(
        "📍 Кнопки переключения режимов внизу ⬇️",
        reply_markup=reply_keyboard
    )
    await message.answer(
        "🔄 <b>Повторить запись</b>\n\n"
        "Выберите услугу из ваших прошлых записей:",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("repeat_booking:"))
async def process_repeat_booking(callback: CallbackQuery, state: FSMContext):
    """Обработка повторной записи"""
    try:
        old_booking_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return

    async with async_session_maker() as session:
        # Получаем старую запись
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        user_bookings = await get_user_bookings(session, user.id)
        old_booking = None
        for b in user_bookings:
            if b.id == old_booking_id:
                old_booking = b
                break

        if not old_booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        # Сохраняем данные из старой записи
        await state.update_data(
            business_id=old_booking.business_id,
            business_name=old_booking.business.name,
            service_id=old_booking.service_id,
            service_name=old_booking.service.name,
            service_duration=old_booking.service.duration_minutes,
            service_price=old_booking.service.price,
            employee_id=old_booking.employee_id,
            employee_name=old_booking.employee.name if old_booking.employee else "Любой мастер"
        )

    # Переходим сразу к выбору даты
    available_dates = []
    for i in range(0, 8):
        future_date = datetime.now().date() + timedelta(days=i)
        if i == 0:
            date_str = f"Сегодня ({future_date.strftime('%d.%m')})"
        elif i == 1:
            date_str = f"Завтра ({future_date.strftime('%d.%m')})"
        else:
            date_str = future_date.strftime('%d.%m.%Y')
        available_dates.append(date_str)

    await callback.message.edit_text(
        f"🔄 Повторная запись\n\n"
        f"🏢 {old_booking.business.name}\n"
        f"💼 {old_booking.service.name}\n"
        f"👤 {old_booking.employee.name if old_booking.employee else 'Любой мастер'}\n\n"
        f"Выберите дату:",
        reply_markup=client_kb.get_dates_keyboard(available_dates)
    )
    await state.set_state(BookingStates.choosing_date)
    await callback.answer()


# ===== ВОЗВРАТ В ГЛАВНОЕ МЕНЮ =====

@router.callback_query(F.data == "client_main_menu")
async def back_to_client_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню клиента"""
    await state.clear()

    # Проверяем роли пользователя
    from database import get_or_create_user, get_user_businesses, get_employee_by_telegram_id
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)

    is_admin = callback.from_user.id in ADMIN_IDS
    show_admin = is_admin or len(businesses) > 0
    show_master = employee is not None

    # Удаляем сообщение с выбором бизнеса
    try:
        await callback.message.delete()
    except:
        pass

    # Отправляем новое сообщение с главным меню
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="👤 Вы в режиме клиента\n\nЗдесь вы можете записаться на услуги и управлять своими записями.",
        reply_markup=client_kb.get_main_menu_keyboard(show_admin_button=show_admin, show_master_button=show_master)
    )
    await callback.answer()
