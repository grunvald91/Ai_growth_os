"""
Обработчики для админ-панели
Управление бизнесами, услугами, сотрудниками и записями
"""
import asyncio
import os
import hashlib
import logging
from datetime import datetime, timedelta, time as _dt_time
import httpx
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from database import async_session_maker, Booking
from bot.config import WEBAPP_URL

logger = logging.getLogger(__name__)
from database.crud import (
    get_or_create_user,
    get_user_businesses,
    create_business,
    create_service,
    create_employee,
    get_business_services,
    get_business_main_services,
    get_business_additional_services,
    get_business_employees,
    get_business_bookings,
    update_booking_status,
    get_business_by_id,
    get_employee_by_telegram_id,
    get_employee_service_ids,
    set_employee_services,
    can_user_create_business,
    get_business_owners_with_roles,
    add_business_owner,
    remove_business_owner,
    get_owners_count,
    get_user_by_telegram_id,
    is_business_owner,
)
from bot.states import BusinessCreationStates, ServiceCreationStates, EmployeeCreationStates, BusinessEditStates, ServiceEditStates, EmployeeEditStates, EmployeeServicesStates, PrepaymentSettingsStates, BusinessOwnerStates, VenueTimePricingStates
from bot.keyboards import admin_kb, client_kb
from bot.utils.validators import format_date_with_weekday, format_phone
from bot.utils.message_utils import safe_edit_or_send

_ROOM_PHOTO_ALBUM_SETTLE_SECONDS = 1.5
_room_photo_album_pending = {}
_room_photo_capture_sessions = {}


def _advance_room_photo_capture(chat_id, user_id, employee_id, open_capture):
    key = (chat_id, user_id, employee_id)
    generation, _ = _room_photo_capture_sessions.get(key, (0, True))
    _room_photo_capture_sessions[key] = (generation + 1, open_capture)


async def _send_room_photo_album_summary(key):
    """Одно подтверждение после того, как Telegram закончил присылать альбом."""
    try:
        await asyncio.sleep(_ROOM_PHOTO_ALBUM_SETTLE_SECONDS)
        batch = _room_photo_album_pending[key]
        await batch["message"].answer(
            f"✅ Добавлено {batch['count']} фото (всего: {batch['total']}).\n\n"
            "Отправьте ещё или нажмите Готово.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data=f"emp_photos_done:{key[3]}", style="success")]
            ]),
        )
    finally:
        batch = _room_photo_album_pending.get(key)
        if batch and batch["task"] is asyncio.current_task():
            _room_photo_album_pending.pop(key, None)


def _cancel_room_photo_album_summaries(callback):
    employee_id = int(callback.data.split(":")[1])
    for key in list(_room_photo_album_pending):
        if key[0] == callback.message.chat.id and key[1] == callback.from_user.id and key[3] == employee_id:
            batch = _room_photo_album_pending.pop(key)
            batch["task"].cancel()


SUPER_ADMIN_ID = 437862772


def _time_to_state(value):
    """Время для хранения в состоянии диалога: строка «ЧЧ:ММ» (состояние хранится как JSON)."""
    return value.strftime("%H:%M") if value else None


def _time_from_state(value):
    """Обратно из состояния диалога в time; принимает и строку «ЧЧ:ММ», и готовый time."""
    if not value:
        return None
    if isinstance(value, str):
        hours, minutes = value.split(":")[:2]
        return _dt_time(int(hours), int(minutes))
    return value


# Путь для кеша медиа-файлов webapp
MEDIA_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'webapp', 'static', 'media_cache')


async def _save_video_to_cache(bot, file_id: str) -> bool:
    """Скачивает видео из Telegram и сохраняет в кеш для webapp.
    Возвращает True если успешно, False если файл слишком большой (>20MB лимит Telegram API)."""
    try:
        os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)
        file_hash = hashlib.md5(file_id.encode()).hexdigest()
        cache_path = os.path.join(MEDIA_CACHE_DIR, file_hash + '.mp4')

        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, cache_path)
        return True
    except Exception:
        return False


async def _save_photo_to_cache(bot, file_id: str) -> bool:
    """Скачивает фото из Telegram и сохраняет в кеш для webapp."""
    try:
        os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)
        file_hash = hashlib.md5(file_id.encode()).hexdigest()
        cache_path = os.path.join(MEDIA_CACHE_DIR, file_hash + '.jpg')

        file = await bot.get_file(file_id)
        await bot.download_file(file.file_path, cache_path)
        return True
    except Exception:
        return False


router = Router()


# ===== ВЕБ-АДМИНКА: ПОЛУЧЕНИЕ КОДА =====

@router.message(F.text == "🔐 Веб-админка")
async def web_admin_get_code(message: Message):
    """Отправить владельцу код для входа в веб-админку через браузер.

    Telegram-WebApp вход работает автоматически (в TG нажми «Аналитика»/«Расписание»),
    а вход с обычного браузера требует ввод телефона + 6-значного кода. Эта кнопка
    запускает существующий /auth/phone флоу — код приходит сюда же в Telegram.
    Роль admin определится автоматически по владению бизнесом.
    """
    tg_id = message.from_user.id
    async with async_session_maker() as session:
        user = await get_user_by_telegram_id(session, tg_id)

    if not user or not user.phone:
        await message.answer(
            "🔐 <b>Вход в веб-админку через браузер</b>\n\n"
            "У вас не привязан номер телефона — поэтому код выслать некуда.\n\n"
            "Откройте веб-админку <b>прямо из Telegram</b> — там вход выполняется автоматически:\n"
            "• Нажмите «📈 Аналитика» или «📅 Расписание» в меню\n\n"
            "Либо привяжите телефон через бота и попробуйте снова.",
            parse_mode="HTML"
        )
        return

    if not WEBAPP_URL:
        await message.answer("WEBAPP_URL не настроен в конфигурации сервера.")
        return

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{WEBAPP_URL}/api/auth/phone",
                json={"phone": user.phone},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"API вернул {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.warning("web-admin code request failed: %s", e)
        await message.answer(
            "Не удалось запросить код. Попробуйте позже или войдите через Telegram WebApp."
        )
        return

    await message.answer(
        "🔐 <b>Веб-админка — вход через браузер</b>\n\n"
        "Я уже отправил вам 6-значный код отдельным сообщением (если оно не пришло — "
        "проверьте, что номер привязан правильно).\n\n"
        f"1. Откройте: {WEBAPP_URL}/login\n"
        f"2. Введите телефон: <code>{user.phone}</code>\n"
        "3. Введите код из сообщения\n\n"
        "Роль администратора определится автоматически по вашему доступу к бизнесам.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# ===== ПРОСМОТР БИЗНЕСОВ =====

@router.message(F.text == "🏢 Мои бизнесы")
async def show_my_businesses(message: Message, state: FSMContext):
    """Показать бизнесы пользователя"""
    # Очищаем FSM состояние при возврате к списку бизнесов
    await state.clear()

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        businesses = await get_user_businesses(session, user.id)

    if not businesses:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        builder.adjust(1)

        await message.answer(
            "У вас пока нет ни одного бизнеса.\n"
            "Используйте '➕ Добавить бизнес' чтобы создать.",
            reply_markup=builder.as_markup()
        )
        return

    await message.answer(
        "Ваши бизнесы:",
        reply_markup=admin_kb.get_businesses_admin_keyboard(businesses)
    )


@router.callback_query(F.data == "admin_back_to_list")
async def admin_back_to_businesses(callback: CallbackQuery, state: FSMContext):
    """Возврат к списку бизнесов (обработчик callback кнопки для админа)"""
    # Очищаем FSM состояние при возврате
    await state.clear()

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        businesses = await get_user_businesses(session, user.id)

    if not businesses:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🏠 Главное меню", callback_data="main_menu")
        builder.adjust(1)

        try:
            await callback.message.edit_text(
                "У вас пока нет ни одного бизнеса.\n"
                "Используйте '➕ Добавить бизнес' чтобы создать.",
                reply_markup=builder.as_markup()
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                "У вас пока нет ни одного бизнеса.\n"
                "Используйте '➕ Добавить бизнес' чтобы создать.",
                reply_markup=builder.as_markup()
            )
        await callback.answer()
        return

    try:
        await callback.message.edit_text(
            "Ваши бизнесы:",
            reply_markup=admin_kb.get_businesses_admin_keyboard(businesses)
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            "Ваши бизнесы:",
            reply_markup=admin_kb.get_businesses_admin_keyboard(businesses)
        )
    await callback.answer()


# ===== СОЗДАНИЕ БИЗНЕСА =====

@router.message(F.text == "➕ Добавить бизнес")
@router.callback_query(F.data == "create_business")
async def start_business_creation(event, state: FSMContext):
    """Начало создания бизнеса"""
    message = event.message if isinstance(event, CallbackQuery) else event
    user_id_tg = event.from_user.id

    # Проверяем, может ли пользователь создать бизнес
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=user_id_tg,
            username=event.from_user.username,
            full_name=event.from_user.full_name
        )
        can_create, reason = await can_user_create_business(session, user.id)

    if not can_create:
        # Проверяем, нужно ли предложить trial подписку
        if "нет активной подписки" in reason.lower() or "не найдена подписка" in reason.lower():
            # Предлагаем активировать trial
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text="🎁 Активировать пробный период (14 дней)", callback_data="activate_trial", style="success")
            builder.button(text="❌ Отмена", callback_data="cancel_trial")
            builder.adjust(1)

            await message.answer(
                "🏢 <b>Создание бизнеса</b>\n\n"
                "Для создания бизнеса нужна подписка.\n\n"
                "🎁 <b>Первые 14 дней — бесплатно!</b>\n"
                "Активируйте пробный период прямо сейчас:",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        else:
            await message.answer(
                f"❌ <b>Невозможно создать бизнес</b>\n\n{reason}",
                parse_mode="HTML"
            )
        if isinstance(event, CallbackQuery):
            await event.answer()
        return

    await message.answer(
        "Создание нового бизнеса\n\n"
        "Введите название вашего бизнеса:"
    )
    await state.set_state(BusinessCreationStates.entering_name)

    if isinstance(event, CallbackQuery):
        await event.answer()


@router.callback_query(F.data == "activate_trial")
async def activate_trial_subscription(callback: CallbackQuery, state: FSMContext):
    """Активация пробного периода"""
    from bot.services.mlm_sync import create_trial_subscription

    success, message = await create_trial_subscription(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        full_name=callback.from_user.full_name,
        tariff="standard",
        trial_days=14
    )

    if success:
        await callback.message.edit_text(
            f"🎉 <b>{message}</b>\n\n"
            "Теперь вы можете создать свой бизнес.\n\n"
            "Введите название вашего бизнеса:",
            parse_mode="HTML"
        )
        await state.set_state(BusinessCreationStates.entering_name)
    else:
        await callback.message.edit_text(
            f"❌ <b>Ошибка</b>\n\n{message}",
            parse_mode="HTML"
        )

    await callback.answer()


@router.callback_query(F.data == "cancel_trial")
async def cancel_trial_activation(callback: CallbackQuery):
    """Отмена активации trial"""
    await callback.message.edit_text("Создание бизнеса отменено.")
    await callback.answer()


@router.message(BusinessCreationStates.entering_name)
async def business_name_entered(message: Message, state: FSMContext):
    """Получение названия бизнеса"""
    await state.update_data(name=message.text)

    await message.answer(
        "Выберите тип бизнеса:",
        reply_markup=admin_kb.get_business_types_keyboard()
    )
    await state.set_state(BusinessCreationStates.entering_type)


@router.callback_query(F.data.startswith("business_type:"), BusinessCreationStates.entering_type)
async def business_type_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбор типа бизнеса"""
    business_type = callback.data.split(":")[1]

    # Получаем шаблон для этого типа бизнеса
    from bot.utils.business_templates import get_template
    template = get_template(business_type)

    # Автоматически применяем шаблон.
    # Время кладём в состояние строками "ЧЧ:ММ": в Redis состояние хранится как JSON,
    # а объект time в JSON не превращается — раньше из-за этого кнопка типа молчала.
    await state.update_data(
        business_type=business_type,
        description=template["description"],
        working_hours_start=_time_to_state(template["working_hours_start"]),
        working_hours_end=_time_to_state(template["working_hours_end"]),
        break_start=_time_to_state(template["break_start"]),
        break_end=_time_to_state(template["break_end"]),
        booking_interval_minutes=template["booking_interval_minutes"]
    )

    template_info = f"""
✅ Применен шаблон "{template['name']}"

⏰ Время работы: {template['working_hours_start'].strftime('%H:%M')}-{template['working_hours_end'].strftime('%H:%M')}
"""
    if template['break_start'] and template['break_end']:
        template_info += f"☕ Перерыв: {template['break_start'].strftime('%H:%M')}-{template['break_end'].strftime('%H:%M')}\n"

    template_info += f"""📊 Шаг записи: {template['booking_interval_minutes']} мин

Теперь введите описание бизнеса
(или '-' чтобы использовать описание из шаблона):"""

    await callback.message.edit_text(template_info)
    await state.set_state(BusinessCreationStates.entering_description)
    await callback.answer()


@router.message(BusinessCreationStates.entering_description)
async def business_description_entered(message: Message, state: FSMContext):
    """Получение описания"""
    # Если пользователь не ввел "-", обновляем описание
    if message.text != "-":
        await state.update_data(description=message.text)

    # Шаблон уже заполнил время работы, перерыв и интервал
    # Переходим сразу к адресу
    await message.answer(
        "Введите адрес бизнеса (или '-' чтобы пропустить):"
    )
    await state.set_state(BusinessCreationStates.entering_address)


@router.message(BusinessCreationStates.entering_address)
async def business_address_entered(message: Message, state: FSMContext):
    """Получение адреса"""
    address = None if message.text == "-" else message.text
    await state.update_data(address=address)

    await message.answer(
        "Введите телефон для связи (или '-' чтобы пропустить):"
    )
    await state.set_state(BusinessCreationStates.entering_phone)


@router.message(BusinessCreationStates.entering_phone)
async def business_phone_entered(message: Message, state: FSMContext):
    """Получение телефона"""
    from bot.utils.validators import validate_phone

    phone = None
    if message.text != "-":
        if not validate_phone(message.text):
            await message.answer(
                "❌ Неверный формат телефона!\n"
                "Используйте формат: +7XXXXXXXXXX или 8XXXXXXXXXX\n\n"
                "Попробуйте снова или '-' чтобы пропустить:"
            )
            return
        # Форматируем телефон: если 10 цифр - добавляем +7
        phone = format_phone(message.text)

    await state.update_data(phone=phone)

    # Создаем бизнес с данными из шаблона
    data = await state.get_data()

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        business = await create_business(
            session,
            owner_id=user.id,
            name=data['name'],
            business_type=data['business_type'],
            description=data.get('description'),
            address=data.get('address'),
            phone=data.get('phone'),
            working_hours_start=_time_from_state(data.get('working_hours_start')),
            working_hours_end=_time_from_state(data.get('working_hours_end')),
            break_start=_time_from_state(data.get('break_start')),
            break_end=_time_from_state(data.get('break_end')),
            booking_interval_minutes=data.get('booking_interval_minutes', 30)
        )

        # Для venue_rental создаём предустановленные услуги-время
        from bot.utils.business_templates import get_template
        from bot.utils.business_types import is_venue_rental_type
        template = get_template(data['business_type'])
        if is_venue_rental_type(data['business_type']):
            if 'default_services' in template:
                for svc in template['default_services']:
                    await create_service(
                        session,
                        business_id=business.id,
                        name=svc['name'],
                        duration_minutes=svc['duration_minutes'],
                        price=svc.get('price')
                    )
            # Создаём доп. услуги из шаблона
            if 'default_additional_services' in template:
                for svc in template['default_additional_services']:
                    await create_service(
                        session,
                        business_id=business.id,
                        name=svc['name'],
                        duration_minutes=svc['duration_minutes'],
                        price=svc.get('price'),
                        is_additional=True
                    )

    # Получаем роли для клавиатуры
    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    # Формируем сообщение в зависимости от типа бизнеса
    from bot.utils.business_types import is_venue_rental_type, get_terminology
    if is_venue_rental_type(data['business_type']):
        success_text = (
            f"✅ Бизнес '{business.name}' успешно создан!\n\n"
            f"⏰ Время работы: {business.working_hours_start.strftime('%H:%M') if business.working_hours_start else 'не указано'}-"
            f"{business.working_hours_end.strftime('%H:%M') if business.working_hours_end else 'не указано'}\n"
            f"📊 Шаг записи: {business.booking_interval_minutes} мин\n\n"
            f"✅ Автоматически созданы варианты времени аренды:\n"
            f"• 1 час, 2 часа, 4 часа, 8 часов\n\n"
            f"🛎 Доп. услуги: Полотенца (200₽), Халат (300₽)\n\n"
            f"💡 Теперь добавьте помещения и установите цены."
        )
    else:
        success_text = (
            f"✅ Бизнес '{business.name}' успешно создан!\n\n"
            f"⏰ Время работы: {business.working_hours_start.strftime('%H:%M') if business.working_hours_start else 'не указано'}-"
            f"{business.working_hours_end.strftime('%H:%M') if business.working_hours_end else 'не указано'}\n"
            f"📊 Шаг записи: {business.booking_interval_minutes} мин"
        )

    await message.answer(
        success_text,
        reply_markup=admin_kb.get_admin_menu_keyboard(show_employee_button=(employee is not None), is_super_admin=(message.from_user.id == 437862772))
    )

    await state.clear()


@router.message(BusinessCreationStates.entering_working_hours)
async def business_working_hours_entered(message: Message, state: FSMContext):
    """Получение времени работы"""
    from datetime import time

    working_hours_start = None
    working_hours_end = None

    if message.text != "-":
        try:
            # Парсим время в формате "09:00-18:00"
            times = message.text.split("-")
            if len(times) == 2:
                start_hour, start_min = map(int, times[0].strip().split(":"))
                end_hour, end_min = map(int, times[1].strip().split(":"))
                working_hours_start = time(start_hour, start_min)
                working_hours_end = time(end_hour, end_min)
        except (ValueError, IndexError):
            await message.answer(
                "❌ Неверный формат времени!\n"
                "Используйте формат: 09:00-18:00\n\n"
                "Попробуйте снова или отправьте '-' чтобы пропустить:"
            )
            return

    await state.update_data(
        working_hours_start=_time_to_state(working_hours_start),
        working_hours_end=_time_to_state(working_hours_end)
    )

    await message.answer(
        "Введите время перерыва (обед) в формате:\n"
        "<b>13:00-14:00</b>\n\n"
        "Или '-' если перерыва нет:",
        parse_mode="HTML"
    )
    await state.set_state(BusinessCreationStates.entering_break_time)


@router.message(BusinessCreationStates.entering_break_time)
async def business_break_time_entered(message: Message, state: FSMContext):
    """Получение времени перерыва"""
    from datetime import time

    break_start = None
    break_end = None

    if message.text != "-":
        try:
            times = message.text.split("-")
            if len(times) == 2:
                start_hour, start_min = map(int, times[0].strip().split(":"))
                end_hour, end_min = map(int, times[1].strip().split(":"))
                break_start = time(start_hour, start_min)
                break_end = time(end_hour, end_min)
        except (ValueError, IndexError):
            await message.answer(
                "❌ Неверный формат времени!\n"
                "Используйте формат: 13:00-14:00\n\n"
                "Попробуйте снова или отправьте '-' чтобы пропустить:"
            )
            return

    await state.update_data(
        break_start=_time_to_state(break_start),
        break_end=_time_to_state(break_end)
    )

    await message.answer(
        "Введите шаг записи в минутах (интервал между записями):\n"
        "Например: <b>30</b> (рекомендуется)\n\n"
        "Или '-' для значения по умолчанию (30 минут):",
        parse_mode="HTML"
    )
    await state.set_state(BusinessCreationStates.entering_booking_interval)


@router.message(BusinessCreationStates.entering_booking_interval)
async def business_booking_interval_entered(message: Message, state: FSMContext):
    """Получение шага записи и создание бизнеса"""
    booking_interval = 30  # По умолчанию

    if message.text != "-":
        try:
            booking_interval = int(message.text)
            if booking_interval <= 0 or booking_interval > 240:
                await message.answer(
                    "❌ Шаг записи должен быть от 1 до 240 минут!\n"
                    "Попробуйте снова:"
                )
                return
        except ValueError:
            await message.answer(
                "❌ Введите число (минуты) или '-' для значения по умолчанию!"
            )
            return

    data = await state.get_data()

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        business = await create_business(
            session,
            owner_id=user.id,
            name=data['name'],
            business_type=data['business_type'],
            description=data.get('description'),
            address=data.get('address'),
            phone=data.get('phone'),
            working_hours_start=_time_from_state(data.get('working_hours_start')),
            working_hours_end=_time_from_state(data.get('working_hours_end')),
            break_start=_time_from_state(data.get('break_start')),
            break_end=_time_from_state(data.get('break_end')),
            booking_interval_minutes=booking_interval
        )

    # Формируем информацию о бизнесе
    info_text = f"""
✅ <b>Бизнес успешно создан!</b>

🏢 {business.name}
"""
    if business.working_hours_start and business.working_hours_end:
        info_text += f"⏰ Время работы: {business.working_hours_start.strftime('%H:%M')}-{business.working_hours_end.strftime('%H:%M')}\n"

    if business.break_start and business.break_end:
        info_text += f"🍽 Перерыв: {business.break_start.strftime('%H:%M')}-{business.break_end.strftime('%H:%M')}\n"

    info_text += f"📊 Шаг записи: {booking_interval} минут\n"
    info_text += "\n💡 Теперь добавьте услуги и сотрудников."

    # Клавиатура для перехода к управлению бизнесом
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="⚙️ Управление бизнесом", callback_data=f"admin_business:{business.id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

    await state.clear()


# ===== СОЗДАНИЕ УСЛУГИ =====

@router.message(F.text == "➕ Добавить услугу")
async def start_service_creation(message: Message, state: FSMContext):
    """Начало создания услуги"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        businesses = await get_user_businesses(session, user.id)

    if not businesses:
        await message.answer(
            "Сначала создайте бизнес!"
        )
        return

    await message.answer(
        "Выберите бизнес, к которому добавить услугу:",
        reply_markup=admin_kb.get_businesses_admin_keyboard(businesses, "add_service_to")
    )
    await state.set_state(ServiceCreationStates.choosing_business)


@router.callback_query(F.data.startswith("add_service_to:"))
async def add_service_to_business(callback: CallbackQuery, state: FSMContext):
    """Добавление услуги к бизнесу (прямой вход)"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        # Получаем тип бизнеса
        business = await get_business_by_id(session, business_id)
        business_type = business.business_type if business else None

    await state.update_data(business_id=business_id, business_type=business_type)

    await callback.message.edit_text(
        "Введите название услуги:"
    )
    await state.set_state(ServiceCreationStates.entering_name)
    await callback.answer()


@router.callback_query(F.data.startswith("add_service_to:"), ServiceCreationStates.choosing_business)
async def business_for_service_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбор бизнеса для услуги"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner, async_session_maker, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        # Получаем тип бизнеса
        business = await get_business_by_id(session, business_id)
        business_type = business.business_type if business else None

    await state.update_data(business_id=business_id, business_type=business_type)

    await callback.message.edit_text(
        "Введите название услуги:"
    )
    await state.set_state(ServiceCreationStates.entering_name)
    await callback.answer()


@router.message(ServiceCreationStates.entering_name)
async def service_name_entered(message: Message, state: FSMContext):
    """Получение названия услуги"""
    await state.update_data(name=message.text)

    await message.answer(
        "Введите описание услуги (или '-' чтобы пропустить):"
    )
    await state.set_state(ServiceCreationStates.entering_description)


@router.message(ServiceCreationStates.entering_description)
async def service_description_entered(message: Message, state: FSMContext):
    """Получение описания услуги"""
    description = None if message.text == "-" else message.text
    await state.update_data(description=description)

    data = await state.get_data()

    # Для доп. услуг пропускаем ввод длительности (auto=0)
    if data.get('is_additional_service'):
        await state.update_data(duration_minutes=0)
        await message.answer(
            "Введите цену в рублях (или '-' чтобы пропустить):"
        )
        await state.set_state(ServiceCreationStates.entering_price)
        return

    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    if is_venue:
        await message.answer(
            "Введите длительность в минутах (например: 60):\n\n"
            "💡 Или '-' для доп. услуг без времени (аренда инвентаря и т.п.)"
        )
    else:
        await message.answer(
            "Введите длительность услуги в минутах (например: 60):"
        )
    await state.set_state(ServiceCreationStates.entering_duration)


@router.message(ServiceCreationStates.entering_duration)
async def service_duration_entered(message: Message, state: FSMContext):
    """Получение длительности"""
    from bot.utils.validators import validate_duration
    from bot.utils.business_types import is_venue_rental_type

    data = await state.get_data()
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    # Для venue_rental разрешаем "-" (без времени)
    if message.text == "-" and is_venue:
        await state.update_data(duration_minutes=0)
        await message.answer(
            "Введите цену в рублях (или '-' чтобы пропустить):"
        )
        await state.set_state(ServiceCreationStates.entering_price)
        return

    is_valid, duration = validate_duration(message.text)
    if not is_valid:
        if is_venue:
            await message.answer(
                "❌ Введите число от 1 до 480 минут или '-' для услуги без времени:"
            )
        else:
            await message.answer(
                "❌ Введите корректное число от 1 до 480 минут (до 8 часов):"
            )
        return

    await state.update_data(duration_minutes=duration)

    await message.answer(
        "Введите цену в рублях (или '-' чтобы пропустить):"
    )
    await state.set_state(ServiceCreationStates.entering_price)


@router.message(ServiceCreationStates.entering_price)
async def service_price_entered(message: Message, state: FSMContext):
    """Получение цены и создание услуги"""
    from bot.utils.validators import validate_price

    price = None
    if message.text != "-":
        is_valid, price_value = validate_price(message.text)
        if not is_valid:
            await message.answer(
                "❌ Введите корректную цену от 0 до 1000000 рублей или '-' чтобы пропустить:"
            )
            return
        price = price_value

    data = await state.get_data()
    is_additional = data.get('is_additional_service', False)
    business_type = data.get('business_type', '')

    # Для venue_rental доп. услуг с ценой — спрашиваем "Цена за час?"
    from bot.utils.business_types import is_venue_rental_type
    if is_additional and price and is_venue_rental_type(business_type):
        await state.update_data(service_price=price)
        await state.set_state(ServiceCreationStates.choosing_hourly)
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="💰 За час", callback_data="hourly_price:yes")
        builder.button(text="🏷 Фиксированная", callback_data="hourly_price:no")
        builder.adjust(2)
        await message.answer(
            f"💰 Цена: {price}₽\n\n"
            "Это цена <b>за час</b> аренды или <b>фиксированная</b>?",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return

    # Спрашиваем тип ценообразования
    await state.update_data(service_price=price)
    await _ask_pricing_type(message, state)


async def _finish_service_creation(message_or_callback, state: FSMContext, price: int, is_hourly: bool = False, pricing_type: str = 'fixed', group_pricing: list = None):
    """Завершение создания услуги (общий код)"""
    import json as _json
    data = await state.get_data()
    business_id = data['business_id']
    is_additional = data.get('is_additional_service', False)
    if group_pricing is None:
        group_pricing = data.get('group_pricing', [])
    if pricing_type == 'fixed':
        pricing_type = data.get('pricing_type', 'fixed')

    async with async_session_maker() as session:
        service = await create_service(
            session,
            business_id=business_id,
            name=data['name'],
            duration_minutes=data['duration_minutes'],
            description=data.get('description'),
            price=price,
            is_additional=is_additional,
            is_hourly_price=is_hourly
        )
        # Save pricing_type and group_pricing
        service.pricing_type = pricing_type
        if group_pricing:
            service.group_pricing = _json.dumps(group_pricing)
        await session.commit()
        await session.refresh(service)

    # Формируем информацию об услуге
    svc_type_label = "🛎 Доп. услуга" if is_additional else "📋"
    price_label = f"{service.price} ₽/ч" if is_hourly else f"{service.price} ₽"
    info_text = f"""
✅ <b>Услуга успешно создана!</b>

{svc_type_label} {service.name}
"""
    if service.duration_minutes and service.duration_minutes > 0:
        info_text += f"⏱ Длительность: {service.duration_minutes} мин\n"
    if service.price:
        info_text += f"💰 Цена: {price_label}\n"
    if pricing_type == 'per_person' and group_pricing:
        info_text += "👥 Тарифы по кол-ву:\n"
        for t in group_pricing:
            info_text += f"  {t['min_people']}–{t['max_people']} чел: {t['price']}₽\n"
    if service.description:
        info_text += f"\n📝 {service.description}"

    # Клавиатура для возврата к списку услуг
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    if is_additional:
        builder.button(text="◀️ К доп. услугам", callback_data=f"manage_additional_services:{business_id}")
    else:
        builder.button(text="◀️ К списку услуг", callback_data=f"manage_services:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    # Отправляем сообщение (работает и для Message, и для callback.message)
    msg = message_or_callback if hasattr(message_or_callback, 'answer') else message_or_callback.message
    await msg.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

    await state.clear()


@router.callback_query(F.data.startswith("hourly_price:"), ServiceCreationStates.choosing_hourly)
async def service_hourly_price_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбор: цена за час или фиксированная"""
    is_hourly = callback.data.split(":")[1] == "yes"
    await state.update_data(is_hourly=is_hourly)
    await _ask_pricing_type(callback.message, state)
    await callback.answer()


async def _ask_pricing_type(msg, state: FSMContext):
    """Спросить тип ценообразования: фиксированная / по кол-ву человек"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Фиксированная цена", callback_data="pricing_type:fixed")
    builder.button(text="👥 По количеству человек", callback_data="pricing_type:per_person")
    builder.adjust(1)
    await state.set_state(ServiceCreationStates.choosing_pricing_type)
    await msg.answer(
        "📊 <b>Тип ценообразования:</b>\n\n"
        "💰 <b>Фиксированная</b> — одна цена для всех\n"
        "👥 <b>По количеству человек</b> — разные тарифы в зависимости от размера группы",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("pricing_type:"), ServiceCreationStates.choosing_pricing_type)
async def service_pricing_type_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбор типа ценообразования"""
    pricing_type = callback.data.split(":")[1]
    await state.update_data(pricing_type=pricing_type, group_pricing=[])

    if pricing_type == "per_person":
        await state.set_state(ServiceCreationStates.entering_group_tier)
        await callback.message.edit_text(
            "👥 <b>Тарифы по количеству человек</b>\n\n"
            "Введите тариф в формате:\n"
            "<code>мин-макс цена</code>\n\n"
            "Например: <code>1-3 500</code> (1–3 чел. = 500₽)\n"
            "Или: <code>4-10 400</code> (4–10 чел. = 400₽)\n\n"
            "Когда закончите, отправьте <b>Готово</b>",
            parse_mode="HTML"
        )
    else:
        data = await state.get_data()
        price = data.get('service_price')
        is_hourly = data.get('is_hourly', False)
        await _finish_service_creation(callback, state, price, is_hourly=is_hourly, pricing_type='fixed')

    await callback.answer()


@router.message(ServiceCreationStates.entering_group_tier)
async def service_group_tier_entered(message: Message, state: FSMContext):
    """Ввод тарифа по количеству человек"""
    import json as _json

    if message.text and message.text.strip().lower() in ("готово", "done", "ok"):
        data = await state.get_data()
        tiers = data.get('group_pricing', [])
        price = data.get('service_price')
        is_hourly = data.get('is_hourly', False)

        if not tiers:
            await message.answer("⚠️ Добавьте хотя бы один тариф или отправьте тариф в формате: <code>мин-макс цена</code>", parse_mode="HTML")
            return

        await _finish_service_creation(message, state, price, is_hourly=is_hourly, pricing_type='per_person', group_pricing=tiers)
        return

    # Parse tier: "1-3 500" or "1 3 500"
    text = message.text.strip()
    import re
    match = re.match(r'(\d+)\s*[-–—]\s*(\d+)\s+(\d+)', text)
    if not match:
        await message.answer(
            "❌ Неверный формат. Используйте: <code>мин-макс цена</code>\n"
            "Например: <code>1-3 500</code>",
            parse_mode="HTML"
        )
        return

    min_p, max_p, price_val = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if min_p > max_p or min_p < 1:
        await message.answer("❌ Минимум должен быть <= максимума и >= 1")
        return

    data = await state.get_data()
    tiers = data.get('group_pricing', [])
    tiers.append({"min_people": min_p, "max_people": max_p, "price": price_val})
    await state.update_data(group_pricing=tiers)

    tiers_text = "\n".join([f"  {t['min_people']}–{t['max_people']} чел: {t['price']}₽" for t in tiers])
    await message.answer(
        f"✅ Тариф добавлен: {min_p}–{max_p} чел = {price_val}₽\n\n"
        f"<b>Текущие тарифы:</b>\n{tiers_text}\n\n"
        "Добавьте ещё тариф или отправьте <b>Готово</b>",
        parse_mode="HTML"
    )


# ===== СОЗДАНИЕ СОТРУДНИКА =====

@router.message(F.text == "👥 Добавить сотрудника")
async def start_employee_creation(message: Message, state: FSMContext):
    """Начало создания сотрудника"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        businesses = await get_user_businesses(session, user.id)

    if not businesses:
        await message.answer("Сначала создайте бизнес!")
        return

    await message.answer(
        "Выберите бизнес, к которому добавить сотрудника:",
        reply_markup=admin_kb.get_businesses_admin_keyboard(businesses, "add_employee_to")
    )
    await state.set_state(EmployeeCreationStates.choosing_business)


@router.callback_query(F.data.startswith("add_employee_to:"))
async def add_employee_to_business(callback: CallbackQuery, state: FSMContext):
    """Добавление сотрудника к бизнесу (прямой вход)"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(business.business_type)

    await state.update_data(business_id=business_id, business_type=business.business_type)

    prompt_text = "🏠 Введите название помещения:" if is_venue else "Введите имя сотрудника:"
    await callback.message.edit_text(prompt_text)
    await state.set_state(EmployeeCreationStates.entering_name)
    await callback.answer()


@router.callback_query(F.data.startswith("add_employee_to:"), EmployeeCreationStates.choosing_business)
async def business_for_employee_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбор бизнеса для сотрудника"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner, async_session_maker, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(business.business_type)

    await state.update_data(business_id=business_id, business_type=business.business_type)

    prompt_text = "🏠 Введите название помещения:" if is_venue else "Введите имя сотрудника:"
    await callback.message.edit_text(prompt_text)
    await state.set_state(EmployeeCreationStates.entering_name)
    await callback.answer()


@router.message(EmployeeCreationStates.entering_name)
async def employee_name_entered(message: Message, state: FSMContext):
    """Получение имени сотрудника/названия помещения"""
    await state.update_data(name=message.text)

    data = await state.get_data()
    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    if is_venue:
        prompt_text = "📝 Введите описание помещения (или '-' чтобы пропустить):"
    else:
        prompt_text = "Введите специализацию (или '-' чтобы пропустить):"

    await message.answer(prompt_text)
    await state.set_state(EmployeeCreationStates.entering_specialization)


@router.message(EmployeeCreationStates.entering_specialization)
async def employee_specialization_entered(message: Message, state: FSMContext):
    """Получение специализации/описания"""
    specialization = None if message.text == "-" else message.text
    await state.update_data(specialization=specialization)

    data = await state.get_data()
    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    if is_venue:
        # Для помещений Telegram ID опционален
        await message.answer(
            "Введите Telegram ID ответственного (или '-' чтобы пропустить):\n\n"
            "💡 <i>Telegram ID нужен, если вы хотите, чтобы кто-то "
            "получал уведомления о бронированиях этого помещения.</i>\n"
            "🔍 Узнать ID можно у бота @userinfobot",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "Введите Telegram ID сотрудника:\n\n"
            "⚠️ <b>Важно!</b> Telegram ID нужен для:\n"
            "• Доступа мастера к своему профилю\n"
            "• Возможности создавать записи\n"
            "• Получения уведомлений о новых записях\n\n"
            "🔍 Узнать ID можно у бота @userinfobot\n"
            "Или '-' чтобы пропустить (мастер не сможет войти в профиль):",
            parse_mode="HTML"
        )
    await state.set_state(EmployeeCreationStates.entering_telegram_id)


@router.message(EmployeeCreationStates.entering_telegram_id)
async def employee_telegram_id_entered(message: Message, state: FSMContext):
    """Получение Telegram ID сотрудника"""
    telegram_id = None

    if message.text != "-":
        try:
            telegram_id = int(message.text)
            if telegram_id <= 0:
                await message.answer(
                    "❌ Telegram ID должен быть положительным числом!\n"
                    "Попробуйте снова или '-' чтобы пропустить:"
                )
                return
        except ValueError:
            await message.answer(
                "❌ Введите корректный Telegram ID (число) или '-' чтобы пропустить:"
            )
            return

    await state.update_data(telegram_id=telegram_id)

    data = await state.get_data()
    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    if is_venue:
        await message.answer(
            "Введите минимальный интервал бронирования для помещения (в минутах):\n"
            "Например: <b>60</b>\n\n"
            "Или '-' чтобы использовать общий интервал бизнеса:",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "Введите индивидуальный шаг записи для этого сотрудника (в минутах):\n"
            "Например: <b>45</b>\n\n"
            "Или '-' чтобы использовать общий шаг салона:",
            parse_mode="HTML"
        )
    await state.set_state(EmployeeCreationStates.entering_booking_interval)


@router.message(EmployeeCreationStates.entering_booking_interval)
async def employee_booking_interval_entered(message: Message, state: FSMContext):
    """Получение шага записи и создание сотрудника/помещения"""
    booking_interval = None

    data = await state.get_data()
    from bot.utils.business_types import is_venue_rental_type
    is_venue = is_venue_rental_type(data.get('business_type', ''))

    if message.text != "-":
        try:
            booking_interval = int(message.text)
            if booking_interval <= 0 or booking_interval > 240:
                error_text = "❌ Интервал должен быть от 1 до 240 минут!\nПопробуйте снова или '-' для общего интервала:" if is_venue else "❌ Шаг записи должен быть от 1 до 240 минут!\nПопробуйте снова или '-' для общего шага:"
                await message.answer(error_text)
                return
        except ValueError:
            error_text = "❌ Введите число (минуты) или '-' для общего интервала!" if is_venue else "❌ Введите число (минуты) или '-' для общего шага салона!"
            await message.answer(error_text)
            return

    business_id = data['business_id']

    async with async_session_maker() as session:
        employee = await create_employee(
            session,
            business_id=business_id,
            name=data['name'],
            specialization=data.get('specialization'),
            telegram_id=data.get('telegram_id'),
            booking_interval_minutes=booking_interval
        )

    # Формируем информацию о сотруднике/помещении
    if is_venue:
        info_text = f"""
✅ <b>Помещение успешно добавлено!</b>

🏠 {employee.name}
"""
        if employee.specialization:
            info_text += f"📝 Описание: {employee.specialization}\n"

        if employee.telegram_id:
            info_text += f"📱 Telegram ID ответственного: {employee.telegram_id}\n"

        if booking_interval:
            info_text += f"📊 Интервал бронирования: {booking_interval} минут\n"
        else:
            info_text += "📊 Используется общий интервал бизнеса\n"
    else:
        info_text = f"""
✅ <b>Сотрудник успешно добавлен!</b>

👔 {employee.name}
"""
        if employee.specialization:
            info_text += f"💼 Специализация: {employee.specialization}\n"

        if employee.telegram_id:
            info_text += f"📱 Telegram ID: {employee.telegram_id}\n"
        else:
            info_text += "⚠️ <b>Telegram ID не указан</b> - мастер не сможет войти в профиль\n"

        if booking_interval:
            info_text += f"📊 Шаг записи: {booking_interval} минут\n"
        else:
            info_text += "📊 Используется общий шаг бизнеса\n"

    # Клавиатура для возврата к списку
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    button_text = "◀️ К списку помещений" if is_venue else "◀️ К списку сотрудников"
    builder.button(text=button_text, callback_data=f"manage_employees:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

    await state.clear()


# ===== ПРОСМОТР ЗАПИСЕЙ =====

@router.message(F.text == "📊 Записи")
async def show_bookings(message: Message):
    """Показать выбор бизнеса для просмотра записей"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        businesses = await get_user_businesses(session, user.id)

        if not businesses:
            from aiogram.utils.keyboard import InlineKeyboardBuilder
            builder = InlineKeyboardBuilder()
            builder.button(text="🏠 Главное меню", callback_data="main_menu")
            builder.adjust(1)

            await message.answer(
                "У вас нет бизнесов!",
                reply_markup=builder.as_markup()
            )
            return

    await message.answer(
        "Выберите бизнес для просмотра записей:",
        reply_markup=admin_kb.get_businesses_admin_keyboard(businesses, "view_bookings")
    )


# ===== УПРАВЛЕНИЕ БИЗНЕСОМ =====

@router.callback_query(F.data.startswith("admin_business:"))
async def manage_business(callback: CallbackQuery, state: FSMContext):
    """Управление бизнесом"""
    import json
    from urllib.parse import quote
    from database.crud import get_business_subscriptions

    business_id = int(callback.data.split(":")[1])

    # ВАЖНО: Очищаем FSM состояние при входе в управление бизнесом
    await state.clear()

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        services = await get_business_services(session, business_id)
        employees = await get_business_employees(session, business_id)
        subscriptions = await get_business_subscriptions(session, business_id)

    from bot.utils.business_types import get_business_type_name, is_venue_rental_type

    # Разделяем сотрудников на администраторов и мастеров/помещения
    administrators = [e for e in employees if e.role in ["administrator", "master_and_admin"]]
    workers = [e for e in employees if e.role in ["master", "master_and_admin"]]
    is_venue = is_venue_rental_type(business.business_type)

    # Формируем полную информацию о бизнесе
    info_text = f"🏢 <b>{business.name}</b>\n\n"
    info_text += f"📋 Тип: {get_business_type_name(business.business_type)}\n"

    if business.description:
        info_text += f"📝 {business.description}\n\n"
    else:
        info_text += "\n"

    # Контакты
    if business.phone:
        formatted_phone = format_phone(business.phone)
        info_text += f"📞 Телефон: {formatted_phone}\n"

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

    # Инструкции как добраться
    if business.directions and business.directions.strip():
        info_text += f"🗺 Как добраться: {business.directions}\n"

    # Администратор
    if administrators:
        admin_names = ", ".join([a.name for a in administrators])
        info_text += f"👔 Администратор: {admin_names}\n"

    info_text += f"\n{'✅ Активен' if business.is_active else '⏸ Неактивен'}\n"

    # Статистика бизнеса
    info_text += "\n<b>📊 Статистика:</b>\n"
    if is_venue:
        info_text += f"⏱ Время аренды: {len(services)}\n"
        info_text += f"🏠 Помещений: {len(workers)}\n"
    else:
        info_text += f"📋 Услуг: {len(services)}\n"
        info_text += f"👥 Сотрудников: {len(workers)}\n"
    info_text += f"🎫 Абонементов: {len(subscriptions)}\n"

    info_text += "\n<i>Выберите действие:</i>"

    # Проверяем наличие фото
    first_photo_id = None
    if business.photos:
        try:
            photos_data = json.loads(business.photos)
            if photos_data and len(photos_data) > 0:
                first_photo_id = photos_data[0]
        except Exception:
            pass

    keyboard = admin_kb.get_business_management_keyboard(business_id, business.business_type)

    # Если есть фото - отправляем с фото, иначе просто текст
    photo_sent = False
    if first_photo_id:
        # Удаляем старое сообщение и отправляем новое с фото
        try:
            await callback.message.delete()
        except Exception:
            pass
        try:
            await callback.message.answer_photo(
                photo=first_photo_id,
                caption=info_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            photo_sent = True
        except Exception:
            # Если фото не удалось отправить (неверный file_id), отправим без фото
            photo_sent = False

    if not photo_sent:
        # Пробуем отредактировать текст, если не получится - удаляем и отправляем новое
        try:
            await callback.message.edit_text(
                info_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=keyboard
            )
        except Exception:
            try:
                await callback.message.delete()
            except Exception:
                pass
            await callback.message.answer(
                info_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=keyboard
            )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_business:"))
async def delete_business_confirm(callback: CallbackQuery):
    """Запрос подтверждения удаления бизнеса"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        business_name = business.name

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить навсегда", callback_data=f"confirm_delete_business:{business_id}", style="danger")
    builder.button(text="❌ Отмена", callback_data=f"admin_business:{business_id}")
    builder.adjust(1)

    await safe_edit_or_send(
        callback,
        f"⚠️ <b>ВНИМАНИЕ!</b>\n\n"
        f"Вы уверены, что хотите удалить бизнес '{business_name}'?\n\n"
        f"Будут удалены:\n"
        f"• Все услуги\n"
        f"• Все сотрудники\n"
        f"• Все записи клиентов\n\n"
        f"Это действие необратимо!",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_business:"))
async def delete_business_confirmed(callback: CallbackQuery):
    """Отправка запроса на удаление бизнеса суперадмину"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        business_name = business.name

        # Считаем статистику бизнеса
        services = await get_business_services(session, business_id)
        employees = await get_business_employees(session, business_id)
        services_count = len(services)
        employees_count = len(employees)

    # Формируем ссылку на владельца
    if callback.from_user.username:
        owner_link = f'@{callback.from_user.username} (<a href="tg://user?id={callback.from_user.id}">{callback.from_user.full_name}</a>)'
    else:
        owner_link = f'<a href="tg://user?id={callback.from_user.id}">{callback.from_user.full_name}</a>'

    # Отправляем запрос суперадмину
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(
        text="✅ Подтвердить удаление",
        callback_data=f"superadmin_approve_delete:{business_id}:{callback.from_user.id}",
        style="danger"
    )
    builder.button(
        text="❌ Отклонить",
        callback_data=f"superadmin_reject_delete:{business_id}:{callback.from_user.id}"
    )
    builder.adjust(1)

    request_text = f"""
🗑 <b>Запрос на удаление бизнеса</b>

🏢 <b>Бизнес:</b> {business_name}
🆔 <b>ID бизнеса:</b> {business_id}

👤 <b>Владелец:</b> {owner_link}
🆔 <b>ID владельца:</b> <code>{callback.from_user.id}</code>

📊 <b>Статистика:</b>
• Услуг: {services_count}
• Сотрудников: {employees_count}

⚠️ При удалении будут удалены все услуги, сотрудники и записи клиентов.
"""

    try:
        await callback.bot.send_message(
            chat_id=SUPER_ADMIN_ID,
            text=request_text,
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    except Exception as e:
        import logging
        logging.error(f"Error sending delete request to super admin: {e}")
        await callback.answer("❌ Ошибка отправки запроса. Попробуйте позже.", show_alert=True)
        return

    # Сообщаем пользователю что запрос отправлен
    await callback.message.edit_text(
        f"📤 <b>Запрос на удаление отправлен</b>\n\n"
        f"Бизнес: {business_name}\n\n"
        f"Ваш запрос на удаление бизнеса отправлен на рассмотрение администратору.\n"
        f"Вы получите уведомление о решении.",
        parse_mode="HTML"
    )
    await callback.answer("Запрос отправлен на рассмотрение")


@router.callback_query(F.data.startswith("superadmin_approve_delete:"))
async def superadmin_approve_delete(callback: CallbackQuery):
    """Суперадмин подтверждает удаление бизнеса"""
    # Проверяем что это суперадмин
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет прав для этого действия", show_alert=True)
        return

    parts = callback.data.split(":")
    business_id = int(parts[1])
    owner_id = int(parts[2])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

        if not business:
            await callback.message.edit_text(
                "❌ Бизнес не найден. Возможно, он уже был удалён."
            )
            await callback.answer()
            return

        business_name = business.name

        # Удаляем бизнес
        await session.delete(business)
        await session.commit()

    # Обновляем сообщение суперадмина
    await callback.message.edit_text(
        f"✅ <b>Бизнес удалён</b>\n\n"
        f"🏢 {business_name}\n"
        f"🆔 ID: {business_id}",
        parse_mode="HTML"
    )

    # Уведомляем владельца
    try:
        await callback.bot.send_message(
            chat_id=owner_id,
            text=f"✅ <b>Ваш запрос одобрен</b>\n\n"
                 f"Бизнес «{business_name}» был удалён.",
            parse_mode="HTML"
        )
    except Exception as e:
        import logging
        logging.error(f"Error notifying owner about deletion approval: {e}")

    await callback.answer("Бизнес удалён")


@router.callback_query(F.data.startswith("superadmin_reject_delete:"))
async def superadmin_reject_delete(callback: CallbackQuery):
    """Суперадмин отклоняет удаление бизнеса"""
    # Проверяем что это суперадмин
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("❌ У вас нет прав для этого действия", show_alert=True)
        return

    parts = callback.data.split(":")
    business_id = int(parts[1])
    owner_id = int(parts[2])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business_name = business.name if business else f"ID {business_id}"

    # Обновляем сообщение суперадмина
    await callback.message.edit_text(
        f"❌ <b>Удаление отклонено</b>\n\n"
        f"🏢 {business_name}\n"
        f"🆔 ID: {business_id}",
        parse_mode="HTML"
    )

    # Уведомляем владельца
    try:
        await callback.bot.send_message(
            chat_id=owner_id,
            text=f"❌ <b>Ваш запрос отклонён</b>\n\n"
                 f"Запрос на удаление бизнеса «{business_name}» был отклонён администратором.",
            parse_mode="HTML"
        )
    except Exception as e:
        import logging
        logging.error(f"Error notifying owner about deletion rejection: {e}")

    await callback.answer("Запрос отклонён")


async def _show_manage_services(callback: CallbackQuery, business_id: int):
    """Отображает список услуг для управления (без проверки прав и очистки FSM)"""
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.utils.business_types import is_venue_rental_type

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        services = await get_business_services(session, business_id)

    builder = InlineKeyboardBuilder()

    is_venue = is_venue_rental_type(business.business_type)
    service_label = "⏱ Время аренды" if is_venue else "📋 Услуги"
    service_label_single = "время аренды" if is_venue else "услугу"
    add_button_text = "➕ Добавить время" if is_venue else "➕ Добавить услугу"

    main_services = [s for s in services if not s.is_additional]

    if not main_services:
        info_text = f"""
🏢 <b>{business.name}</b>
{service_label}

{"Время аренды пока не добавлено" if is_venue else "Услуги пока не добавлены"}
"""
    else:
        info_text = f"""
🏢 <b>{business.name}</b>
{service_label}

Выберите {service_label_single} для редактирования или удаления:
"""
        for i, service in enumerate(main_services):
            price_text = f", {service.price} ₽" if service.price else ""
            if service.duration_minutes and service.duration_minutes > 0:
                duration_text = f"{service.duration_minutes} мин"
            else:
                duration_text = ""
            if duration_text and price_text:
                info = f" ({duration_text}{price_text})"
            elif duration_text:
                info = f" ({duration_text})"
            elif price_text:
                info = f" ({service.price} ₽)"
            else:
                info = ""
            up_cb = f"svc_up:{service.id}:{business_id}" if i > 0 else "noop"
            dn_cb = f"svc_down:{service.id}:{business_id}" if i < len(main_services) - 1 else "noop"
            builder.button(text="⬆️", callback_data=up_cb)
            builder.button(text=f"📋 {service.name}{info}", callback_data=f"edit_service:{service.id}")
            builder.button(text="⬇️", callback_data=dn_cb)

    builder.button(text=add_button_text, callback_data=f"add_service_to:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    adjust = [3] * len(main_services) + [1, 1, 1]
    builder.adjust(*adjust)

    await safe_edit_or_send(callback, info_text, reply_markup=builder.as_markup())


@router.callback_query(F.data.startswith("manage_services:"))
async def manage_services(callback: CallbackQuery, state: FSMContext):
    """Управление услугами бизнеса"""
    business_id = int(callback.data.split(":")[1])

    # ВАЖНО: Очищаем FSM при возврате к списку услуг
    await state.clear()

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

    await _show_manage_services(callback, business_id)
    await callback.answer()


@router.callback_query(F.data.startswith("svc_up:") | F.data.startswith("svc_down:"))
async def reorder_service(callback: CallbackQuery):
    """Переместить услугу вверх или вниз в списке"""
    parts = callback.data.split(":")
    direction = parts[0]  # "svc_up" или "svc_down"
    service_id = int(parts[1])
    business_id = int(parts[2])

    from sqlalchemy import select
    from database.models import Service

    async with async_session_maker() as session:
        # Все основные услуги бизнеса (уже отсортированы по sort_order, id)
        result = await session.execute(
            select(Service).where(
                Service.business_id == business_id,
                Service.is_active == True,
                Service.is_additional == False
            ).order_by(Service.sort_order, Service.id)
        )
        services = list(result.scalars().all())

        # Инициализируем sort_order если все равны 0
        if all(s.sort_order == 0 for s in services):
            for i, s in enumerate(services):
                s.sort_order = (i + 1) * 10
            await session.commit()
            # Перечитываем
            result = await session.execute(
                select(Service).where(
                    Service.business_id == business_id,
                    Service.is_active == True,
                    Service.is_additional == False
                ).order_by(Service.sort_order, Service.id)
            )
            services = list(result.scalars().all())

        # Находим индекс текущей услуги
        idx = next((i for i, s in enumerate(services) if s.id == service_id), None)
        if idx is None:
            await callback.answer("Услуга не найдена", show_alert=True)
            return

        # Меняем sort_order с соседом
        if direction == "svc_up" and idx > 0:
            services[idx].sort_order, services[idx - 1].sort_order = (
                services[idx - 1].sort_order, services[idx].sort_order
            )
            await session.commit()
            await callback.answer("⬆️ Услуга перемещена вверх")
        elif direction == "svc_down" and idx < len(services) - 1:
            services[idx].sort_order, services[idx + 1].sort_order = (
                services[idx + 1].sort_order, services[idx].sort_order
            )
            await session.commit()
            await callback.answer("⬇️ Услуга перемещена вниз")
        else:
            await callback.answer()
            return

    # Перезагружаем список услуг
    await _show_manage_services(callback, business_id)


@router.callback_query(F.data.startswith("manage_additional_services:"))
async def manage_additional_services(callback: CallbackQuery, state: FSMContext):
    """Управление дополнительными услугами бизнеса (отдельный экран)"""
    business_id = int(callback.data.split(":")[1])

    await state.clear()

    from database import is_business_owner
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        additional_services = await get_business_additional_services(session, business_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    if not additional_services:
        info_text = f"""
🏢 <b>{business.name}</b>
🛎 Дополнительные услуги

Дополнительные услуги пока не добавлены
"""
    else:
        info_text = f"""
🏢 <b>{business.name}</b>
🛎 Дополнительные услуги

Выберите услугу для редактирования или удаления:
"""
        for service in additional_services:
            hourly_mark = "/ч" if getattr(service, 'is_hourly_price', False) else ""
            price_text = f" ({service.price} ₽{hourly_mark})" if service.price else ""
            builder.button(
                text=f"🛎 {service.name}{price_text}",
                callback_data=f"edit_service:{service.id}"
            )

    builder.button(text="➕ Добавить доп. услугу", callback_data=f"add_additional_service_to:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await safe_edit_or_send(
        callback,
        info_text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_additional_service_to:"))
async def add_additional_service_to_business(callback: CallbackQuery, state: FSMContext):
    """Добавление дополнительной услуги к бизнесу (venue_rental)"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        business_type = business.business_type if business else None

    await state.update_data(
        business_id=business_id,
        business_type=business_type,
        is_additional_service=True
    )

    await callback.message.edit_text(
        "🛎 Добавление дополнительной услуги\n\nВведите название услуги:"
    )
    await state.set_state(ServiceCreationStates.entering_name)
    await callback.answer()


@router.callback_query(F.data.startswith("manage_employees:"))
async def manage_employees(callback: CallbackQuery, state: FSMContext):
    """Управление сотрудниками бизнеса"""
    business_id = int(callback.data.split(":")[1])

    # ВАЖНО: Очищаем FSM при возврате к списку сотрудников
    await state.clear()

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        employees = await get_business_employees(session, business_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from bot.utils.business_types import is_venue_rental_type

    builder = InlineKeyboardBuilder()

    # Определяем терминологию
    is_venue = is_venue_rental_type(business.business_type)
    employee_label = "🏠 Помещения" if is_venue else "👥 Сотрудники"
    employee_label_single = "помещение" if is_venue else "сотрудника"
    add_button_text = "➕ Добавить помещение" if is_venue else "➕ Добавить сотрудника"
    icon = "🏠" if is_venue else "👤"

    if not employees:
        info_text = f"""
🏢 <b>{business.name}</b>
{employee_label}

{"Помещения пока не добавлены" if is_venue else "Сотрудники пока не добавлены"}
"""
    else:
        info_text = f"""
🏢 <b>{business.name}</b>
{employee_label}

Выберите {employee_label_single} для редактирования или удаления:
"""
        for employee in employees:
            spec_text = f" ({employee.specialization})" if employee.specialization else ""
            builder.button(
                text=f"{icon} {employee.name}{spec_text}",
                callback_data=f"edit_employee:{employee.id}"
            )

    builder.button(text=add_button_text, callback_data=f"add_employee_to:{business_id}")
    builder.button(text="👔 Назначить администратора", callback_data=f"add_administrator:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await safe_edit_or_send(
        callback,
        info_text,
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_service:"))
async def edit_service_menu(callback: CallbackQuery):
    """Меню редактирования услуги"""
    service_id = int(callback.data.split(":")[1])

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        business = await get_business_by_id(session, service.business_id)

    import json as _json_edit
    # Длительность: показываем "без времени" для 0
    duration_text = f"{service.duration_minutes} мин" if service.duration_minutes and service.duration_minutes > 0 else "без времени"
    price_text = f"{service.price} ₽" if service.price else "(не указана)"

    pricing_type = getattr(service, 'pricing_type', 'fixed') or 'fixed'
    pricing_label = "👥 По кол-ву человек" if pricing_type == 'per_person' else "💰 Фиксированная"

    # Parse group pricing
    group_pricing_text = ""
    gp_raw = getattr(service, 'group_pricing', None)
    if gp_raw and pricing_type == 'per_person':
        try:
            tiers = _json_edit.loads(gp_raw) if isinstance(gp_raw, str) else gp_raw
            if tiers:
                group_pricing_text = "\n👥 Тарифы:\n"
                for t in tiers:
                    group_pricing_text += f"  {t['min_people']}–{t['max_people']} чел: {t['price']}₽\n"
        except (ValueError, TypeError, KeyError):
            pass

    svc_icon = "🛎" if service.is_additional else "📋"
    info_text = f"""
{svc_icon} <b>{service.name}</b>

🏢 Бизнес: {business.name}
⏱ Длительность: {duration_text}
💰 Цена: {price_text}
📊 Тип цены: {pricing_label}{group_pricing_text}
📝 Описание: {service.description if service.description else '(не указано)'}

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать название", callback_data=f"edit_service_name:{service_id}")
    builder.button(text="⏱ Редактировать длительность", callback_data=f"edit_service_duration:{service_id}")
    builder.button(text="💰 Редактировать цену", callback_data=f"edit_service_price:{service_id}")
    builder.button(text="📊 Тип ценообразования", callback_data=f"edit_service_pricing:{service_id}")
    builder.button(text="📝 Редактировать описание", callback_data=f"edit_service_description:{service_id}")
    builder.button(text="🗑 Удалить услугу", callback_data=f"delete_service:{service_id}", style="danger")
    if service.is_additional:
        builder.button(text="◀️ Назад", callback_data=f"manage_additional_services:{service.business_id}")
    else:
        builder.button(text="◀️ Назад", callback_data=f"manage_services:{service.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("delete_service:"))
async def delete_service_confirm(callback: CallbackQuery):
    """Запрос подтверждения удаления услуги"""
    service_id = int(callback.data.split(":")[1])

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service_name = service.name

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, удалить", callback_data=f"confirm_delete_service:{service_id}", style="danger")
    builder.button(text="❌ Отмена", callback_data=f"edit_service:{service_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить услугу '{service_name}'?\n\n"
        "Это действие необратимо!",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_delete_service:"))
async def delete_service_confirmed(callback: CallbackQuery):
    """Окончательное удаление услуги"""
    service_id = int(callback.data.split(":")[1])

    from database.crud import get_service_by_id, delete_service
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        business_id = service.business_id
        service_name = service.name
        was_additional = service.is_additional

        await delete_service(session, service_id)

    await callback.answer(f"✅ '{service_name}' удалено", show_alert=True)

    # Для доп. услуг возвращаемся в экран доп. услуг
    if was_additional:
        callback.data = f"manage_additional_services:{business_id}"
        await manage_additional_services(callback, None)
        return

    # Возвращаемся в список услуг
    from database import is_business_owner, get_or_create_user
    from bot.utils.business_types import is_venue_rental_type
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        business = await get_business_by_id(session, business_id)
        services = await get_business_services(session, business_id)

    is_venue = is_venue_rental_type(business.business_type)
    service_label = "⏱ Время аренды" if is_venue else "📋 Услуги"
    add_button = "➕ Добавить время" if is_venue else "➕ Добавить услугу"

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    main_services = [s for s in services if not s.is_additional]

    if not main_services:
        empty_text = "Время аренды пока не добавлено" if is_venue else "Услуги пока не добавлены"
        info_text = f"""
🏢 <b>{business.name}</b>
{service_label}

{empty_text}
"""
    else:
        select_text = "время для редактирования:" if is_venue else "услугу для редактирования:"
        info_text = f"""
🏢 <b>{business.name}</b>
{service_label}

Выберите {select_text}
"""
        for svc in main_services:
            price_text = f", {svc.price} ₽" if svc.price else ""
            if svc.duration_minutes and svc.duration_minutes > 0:
                duration_text = f"{svc.duration_minutes} мин"
            else:
                duration_text = ""
            if duration_text and price_text:
                info = f" ({duration_text}{price_text})"
            elif duration_text:
                info = f" ({duration_text})"
            elif price_text:
                info = f" ({svc.price} ₽)"
            else:
                info = ""
            builder.button(
                text=f"📋 {svc.name}{info}",
                callback_data=f"edit_service:{svc.id}"
            )

    builder.button(text=add_button, callback_data=f"add_service_to:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("edit_employee:"))
async def edit_employee_menu(callback: CallbackQuery):
    """Меню редактирования сотрудника"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        business = await get_business_by_id(session, employee.business_id)

    # Определяем отображение роли и наличие функций мастера
    if employee.role == "administrator":
        role_text = "🎭 Роль: Администратор\n"
        has_master_functions = False
    elif employee.role == "master_and_admin":
        role_text = "🎭 Роль: Администратор + Мастер\n"
        has_master_functions = True
    else:
        role_text = ""  # Для обычных мастеров роль не показываем
        has_master_functions = True

    interval_text = f"📅 Интервал записи: {employee.booking_interval_minutes} мин\n" if has_master_functions else ""

    # Дополнительная информация для venue_rental
    from bot.utils.business_types import is_venue_rental_type as _is_venue
    venue_info = ""
    if _is_venue(business.business_type):
        if employee.capacity:
            venue_info += f"👥 Вместимость: {employee.capacity} чел.\n"
        if employee.area_sqm:
            venue_info += f"📐 Площадь: {employee.area_sqm} м²\n"
        if employee.description:
            venue_info += f"📝 {employee.description}\n"
        if employee.min_booking_minutes:
            venue_info += f"⏱ Мин. бронирование: {employee.min_booking_minutes} мин\n"
        import json as _emp_json
        photo_count = 0
        if employee.photos:
            try:
                photo_count = len(_emp_json.loads(employee.photos))
            except Exception:
                pass
        venue_info += f"📷 Фото: {photo_count}\n"
        venue_info += f"🎥 Видео: {'есть' if employee.video else 'нет'}\n"

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
{role_text}💼 Специализация: {employee.specialization}
{interval_text}{venue_info}{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📱 Редактировать Telegram ID", callback_data=f"edit_employee_telegram:{employee_id}")
    # Для администратора без функций мастера не показываем кнопку редактирования интервала
    if has_master_functions:
        builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
        builder.button(text="📋 Управление услугами", callback_data=f"manage_emp_services:{employee_id}")
    # Кнопки для аренды помещений (venue_rental)
    from bot.utils.business_types import is_venue_rental_type
    if is_venue_rental_type(business.business_type):
        builder.button(text="💰 Тарифы по времени", callback_data=f"venue_time_pricing:{employee_id}")
        builder.button(text="👥 Вместимость", callback_data=f"edit_emp_capacity:{employee_id}")
        builder.button(text="📐 Площадь", callback_data=f"edit_emp_area:{employee_id}")
        builder.button(text="📝 Описание", callback_data=f"edit_emp_description:{employee_id}")
        builder.button(text="⏱ Мин. время бронирования", callback_data=f"edit_emp_min_booking:{employee_id}")
        builder.button(text="📷 Фото", callback_data=f"edit_emp_photos:{employee_id}")
        builder.button(text="🎥 Видео", callback_data=f"edit_emp_video:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ===== УПРАВЛЕНИЕ УСЛУГАМИ МАСТЕРА =====

@router.callback_query(F.data.startswith("manage_emp_services:"))
async def manage_employee_services(callback: CallbackQuery, state: FSMContext):
    """Управление услугами, которые может делать мастер"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        business = await get_business_by_id(session, employee.business_id)
        services = await get_business_services(session, employee.business_id)
        selected_service_ids = await get_employee_service_ids(session, employee_id)

    if not services:
        await callback.answer("В этом бизнесе пока нет услуг", show_alert=True)
        return

    # Сохраняем выбранные услуги в FSM
    await state.update_data(selected_service_ids=selected_service_ids)
    await state.set_state(EmployeeServicesStates.selecting_services)

    info_text = f"""
📋 <b>Управление услугами мастера</b>

👤 Мастер: {employee.name}
🏢 Бизнес: {business.name}

Выберите услуги, которые может делать этот мастер:
<i>(отмеченные ✅ — назначенные услуги)</i>
"""

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_employee_services_keyboard(
            services, selected_service_ids, employee_id, employee.business_id
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("toggle_emp_svc:"), EmployeeServicesStates.selecting_services)
async def toggle_employee_service(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора услуги для мастера"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    service_id = int(parts[2])

    data = await state.get_data()
    selected_service_ids = data.get("selected_service_ids", [])

    # Переключаем выбор
    if service_id in selected_service_ids:
        selected_service_ids.remove(service_id)
    else:
        selected_service_ids.append(service_id)

    await state.update_data(selected_service_ids=selected_service_ids)

    # Обновляем клавиатуру
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        business = await get_business_by_id(session, employee.business_id)
        services = await get_business_services(session, employee.business_id)

    info_text = f"""
📋 <b>Управление услугами мастера</b>

👤 Мастер: {employee.name}
🏢 Бизнес: {business.name}

Выберите услуги, которые может делать этот мастер:
<i>(отмеченные ✅ — назначенные услуги)</i>
"""

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_employee_services_keyboard(
            services, selected_service_ids, employee_id, employee.business_id
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("save_emp_services:"), EmployeeServicesStates.selecting_services)
async def save_employee_services(callback: CallbackQuery, state: FSMContext):
    """Сохранение выбранных услуг для мастера"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    business_id = int(parts[2])

    data = await state.get_data()
    selected_service_ids = data.get("selected_service_ids", [])

    # Сохраняем в базу
    async with async_session_maker() as session:
        await set_employee_services(session, employee_id, selected_service_ids)

    await state.clear()
    await callback.answer(f"✅ Услуги сохранены ({len(selected_service_ids)} шт.)", show_alert=True)

    # Возвращаемся к редактированию сотрудника
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        business = await get_business_by_id(session, employee.business_id)

    # Определяем отображение роли и наличие функций мастера
    if employee.role == "administrator":
        role_text = "🎭 Роль: Администратор\n"
        has_master_functions = False
    elif employee.role == "master_and_admin":
        role_text = "🎭 Роль: Администратор + Мастер\n"
        has_master_functions = True
    else:
        role_text = ""
        has_master_functions = True

    interval_text = f"📅 Интервал записи: {employee.booking_interval_minutes} мин\n" if has_master_functions else ""

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
{role_text}💼 Специализация: {employee.specialization}
{interval_text}{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Услуги сохранены!

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📱 Редактировать Telegram ID", callback_data=f"edit_employee_telegram:{employee_id}")
    if has_master_functions:
        builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
        builder.button(text="📋 Управление услугами", callback_data=f"manage_emp_services:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("delete_employee:"))
async def delete_employee_confirm(callback: CallbackQuery):
    """Подтверждение удаления сотрудника"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id, delete_employee
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        business_id = employee.business_id
        employee_name = employee.name

        await delete_employee(session, employee_id)

    await callback.answer(f"✅ Сотрудник '{employee_name}' удалён", show_alert=True)

    # Возвращаемся в список сотрудников
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        business = await get_business_by_id(session, business_id)
        employees = await get_business_employees(session, business_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    if not employees:
        info_text = f"""
🏢 <b>{business.name}</b>
👥 <b>Сотрудники</b>

Сотрудники пока не добавлены
"""
    else:
        info_text = f"""
🏢 <b>{business.name}</b>
👥 <b>Сотрудники</b>

Выберите сотрудника для редактирования или удаления:
"""
        for emp in employees:
            builder.button(
                text=f"👤 {emp.name} ({emp.specialization})",
                callback_data=f"edit_employee:{emp.id}"
            )

    builder.button(text="➕ Добавить сотрудника", callback_data=f"add_employee_to:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


# ===== РЕДАКТИРОВАНИЕ УСЛУГ =====

@router.callback_query(F.data.startswith("edit_service_name:"))
async def edit_service_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования названия услуги"""
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)

    await callback.message.edit_text("Введите новое название услуги:")
    await state.set_state(ServiceEditStates.editing_name)
    await callback.answer()


@router.message(ServiceEditStates.editing_name)
async def edit_service_name_save(message: Message, state: FSMContext):
    """Сохранение нового названия услуги"""
    data = await state.get_data()
    service_id = data['service_id']

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service.name = message.text
        await session.commit()
        await session.refresh(service)

        business = await get_business_by_id(session, service.business_id)

    info_text = f"""
📋 <b>{service.name}</b>

🏢 Бизнес: {business.name}
⏱ Длительность: {service.duration_minutes} мин
💰 Цена: {service.price} ₽
📝 Описание: {service.description if service.description else '(не указано)'}

✅ Название успешно изменено на: <b>{message.text}</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать название", callback_data=f"edit_service_name:{service_id}")
    builder.button(text="⏱ Редактировать длительность", callback_data=f"edit_service_duration:{service_id}")
    builder.button(text="💰 Редактировать цену", callback_data=f"edit_service_price:{service_id}")
    builder.button(text="📝 Редактировать описание", callback_data=f"edit_service_description:{service_id}")
    builder.button(text="🗑 Удалить услугу", callback_data=f"delete_service:{service_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_services:{service.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("edit_service_duration:"))
async def edit_service_duration_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования длительности услуги"""
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)

    await callback.message.edit_text("Введите новую длительность услуги (в минутах):")
    await state.set_state(ServiceEditStates.editing_duration)
    await callback.answer()


@router.message(ServiceEditStates.editing_duration)
async def edit_service_duration_save(message: Message, state: FSMContext):
    """Сохранение новой длительности услуги"""
    data = await state.get_data()
    service_id = data['service_id']

    try:
        duration = int(message.text)
        if duration <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Ошибка! Введите корректное число минут (например: 30, 60, 90)")
        return

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service.duration_minutes = duration
        await session.commit()
        await session.refresh(service)

        business = await get_business_by_id(session, service.business_id)

    info_text = f"""
📋 <b>{service.name}</b>

🏢 Бизнес: {business.name}
⏱ Длительность: {service.duration_minutes} мин
💰 Цена: {service.price} ₽
📝 Описание: {service.description if service.description else '(не указано)'}

✅ Длительность успешно изменена на: <b>{duration} мин</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать название", callback_data=f"edit_service_name:{service_id}")
    builder.button(text="⏱ Редактировать длительность", callback_data=f"edit_service_duration:{service_id}")
    builder.button(text="💰 Редактировать цену", callback_data=f"edit_service_price:{service_id}")
    builder.button(text="📝 Редактировать описание", callback_data=f"edit_service_description:{service_id}")
    builder.button(text="🗑 Удалить услугу", callback_data=f"delete_service:{service_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_services:{service.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("edit_service_price:"))
async def edit_service_price_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования цены услуги"""
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)

    await callback.message.edit_text("Введите новую цену услуги (в рублях):")
    await state.set_state(ServiceEditStates.editing_price)
    await callback.answer()


@router.message(ServiceEditStates.editing_price)
async def edit_service_price_save(message: Message, state: FSMContext):
    """Сохранение новой цены услуги"""
    data = await state.get_data()
    service_id = data['service_id']

    try:
        price = float(message.text.replace(',', '.'))
        if price < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Ошибка! Введите корректную цену (например: 1000, 1500.50)")
        return

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service.price = price
        await session.commit()
        await session.refresh(service)

        business = await get_business_by_id(session, service.business_id)

    info_text = f"""
📋 <b>{service.name}</b>

🏢 Бизнес: {business.name}
⏱ Длительность: {service.duration_minutes} мин
💰 Цена: {service.price} ₽
📝 Описание: {service.description if service.description else '(не указано)'}

✅ Цена успешно изменена на: <b>{price} ₽</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать название", callback_data=f"edit_service_name:{service_id}")
    builder.button(text="⏱ Редактировать длительность", callback_data=f"edit_service_duration:{service_id}")
    builder.button(text="💰 Редактировать цену", callback_data=f"edit_service_price:{service_id}")
    builder.button(text="📝 Редактировать описание", callback_data=f"edit_service_description:{service_id}")
    builder.button(text="🗑 Удалить услугу", callback_data=f"delete_service:{service_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_services:{service.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("edit_service_description:"))
async def edit_service_description_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования описания услуги"""
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)

    await callback.message.edit_text("Введите новое описание услуги (или '-' для удаления):")
    await state.set_state(ServiceEditStates.editing_description)
    await callback.answer()


@router.message(ServiceEditStates.editing_description)
async def edit_service_description_save(message: Message, state: FSMContext):
    """Сохранение нового описания услуги"""
    data = await state.get_data()
    service_id = data['service_id']

    description = None if message.text == "-" else message.text

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service.description = description
        await session.commit()
        await session.refresh(service)

        business = await get_business_by_id(session, service.business_id)

    description_display = description if description else "(не указано)"

    info_text = f"""
📋 <b>{service.name}</b>

🏢 Бизнес: {business.name}
⏱ Длительность: {service.duration_minutes} мин
💰 Цена: {service.price} ₽
📝 Описание: {service.description if service.description else '(не указано)'}

✅ Описание успешно обновлено: <i>{description_display}</i>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать название", callback_data=f"edit_service_name:{service_id}")
    builder.button(text="⏱ Редактировать длительность", callback_data=f"edit_service_duration:{service_id}")
    builder.button(text="💰 Редактировать цену", callback_data=f"edit_service_price:{service_id}")
    builder.button(text="📝 Редактировать описание", callback_data=f"edit_service_description:{service_id}")
    builder.button(text="🗑 Удалить услугу", callback_data=f"delete_service:{service_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_services:{service.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


# ===== ТАРИФЫ ПО КОЛ-ВУ ЧЕЛОВЕК (РЕДАКТИРОВАНИЕ) =====

@router.callback_query(F.data.startswith("edit_service_pricing:"))
async def edit_service_pricing_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования типа ценообразования"""
    service_id = int(callback.data.split(":")[1])
    await state.update_data(service_id=service_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 Фиксированная цена", callback_data=f"set_pricing_type:fixed:{service_id}")
    builder.button(text="👥 По количеству человек", callback_data=f"set_pricing_type:per_person:{service_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_service:{service_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        "📊 <b>Выберите тип ценообразования:</b>\n\n"
        "💰 <b>Фиксированная</b> — одна цена для всех\n"
        "👥 <b>По количеству человек</b> — разные тарифы для разного кол-ва гостей",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("set_pricing_type:"))
async def set_pricing_type(callback: CallbackQuery, state: FSMContext):
    """Установить тип ценообразования"""
    import json as _json_pt
    parts = callback.data.split(":")
    pricing_type = parts[1]
    service_id = int(parts[2])

    from database.crud import get_service_by_id
    async with async_session_maker() as session:
        service = await get_service_by_id(session, service_id)
        service.pricing_type = pricing_type
        if pricing_type == 'fixed':
            service.group_pricing = None
        await session.commit()

    if pricing_type == 'per_person':
        await state.update_data(service_id=service_id, group_pricing=[])
        await state.set_state(ServiceEditStates.entering_group_tier)
        await callback.message.edit_text(
            "👥 <b>Тарифы по количеству человек</b>\n\n"
            "Введите тариф в формате:\n"
            "<code>мин-макс цена</code>\n\n"
            "Например: <code>1-3 500</code> (1–3 чел. = 500₽)\n\n"
            "Когда закончите, отправьте <b>Готово</b>",
            parse_mode="HTML"
        )
    else:
        await callback.answer("✅ Тип установлен: Фиксированная цена")
        # Return to edit menu
        await edit_service_menu.__wrapped__(callback) if hasattr(edit_service_menu, '__wrapped__') else None
        # Simulate re-entering the edit menu
        callback.data = f"edit_service:{service_id}"
        await edit_service_menu(callback)

    await callback.answer()


@router.message(ServiceEditStates.entering_group_tier)
async def edit_service_group_tier_entered(message: Message, state: FSMContext):
    """Ввод тарифа при редактировании услуги"""
    import json as _json_gt
    import re

    if message.text and message.text.strip().lower() in ("готово", "done", "ok"):
        data = await state.get_data()
        service_id = data['service_id']
        tiers = data.get('group_pricing', [])

        from database.crud import get_service_by_id
        async with async_session_maker() as session:
            service = await get_service_by_id(session, service_id)
            service.group_pricing = _json_gt.dumps(tiers) if tiers else None
            await session.commit()

        if tiers:
            tiers_text = "\n".join([f"  {t['min_people']}–{t['max_people']} чел: {t['price']}₽" for t in tiers])
            await message.answer(f"✅ Тарифы сохранены:\n{tiers_text}")
        else:
            await message.answer("✅ Тарифы очищены")

        await state.clear()

        # Show edit menu again
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="◀️ К услуге", callback_data=f"edit_service:{service_id}")
        builder.adjust(1)
        await message.answer("Вернуться к редактированию?", reply_markup=builder.as_markup())
        return

    text = message.text.strip()
    match = re.match(r'(\d+)\s*[-–—]\s*(\d+)\s+(\d+)', text)
    if not match:
        await message.answer(
            "❌ Неверный формат. Используйте: <code>мин-макс цена</code>\nНапример: <code>1-3 500</code>",
            parse_mode="HTML"
        )
        return

    min_p, max_p, price_val = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if min_p > max_p or min_p < 1:
        await message.answer("❌ Минимум должен быть <= максимума и >= 1")
        return

    data = await state.get_data()
    tiers = data.get('group_pricing', [])
    tiers.append({"min_people": min_p, "max_people": max_p, "price": price_val})
    await state.update_data(group_pricing=tiers)

    tiers_text = "\n".join([f"  {t['min_people']}–{t['max_people']} чел: {t['price']}₽" for t in tiers])
    await message.answer(
        f"✅ Тариф добавлен: {min_p}–{max_p} чел = {price_val}₽\n\n"
        f"<b>Текущие тарифы:</b>\n{tiers_text}\n\n"
        "Добавьте ещё тариф или отправьте <b>Готово</b>",
        parse_mode="HTML"
    )


# ===== РЕДАКТИРОВАНИЕ СОТРУДНИКОВ =====

@router.callback_query(F.data.startswith("edit_employee_name:"))
async def edit_employee_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования имени сотрудника"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id)

    await callback.message.edit_text("Введите новое имя сотрудника:")
    await state.set_state(EmployeeEditStates.editing_name)
    await callback.answer()


@router.message(EmployeeEditStates.editing_name)
async def edit_employee_name_save(message: Message, state: FSMContext):
    """Сохранение нового имени сотрудника"""
    data = await state.get_data()
    employee_id = data['employee_id']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.name = message.text
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    # Определяем наличие функций мастера
    has_master_functions = employee.role in ("master", "master_and_admin")
    interval_text = f"📅 Интервал записи: {employee.booking_interval_minutes} мин\n" if has_master_functions else ""

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
💼 Специализация: {employee.specialization}
{interval_text}{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Имя успешно изменено на: <b>{message.text}</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    if has_master_functions:
        builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("edit_employee_spec:"))
async def edit_employee_spec_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования специализации сотрудника"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id)

    # Проверяем роль сотрудника
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    if employee.role == "administrator":
        # Для чистого администратора
        builder.button(text="✏️ Изменить текст специализации", callback_data=f"spec_change_text:{employee_id}")
        builder.button(text="🔄 Стать мастером (вести записи)", callback_data=f"spec_become_master:{employee_id}")
        builder.button(text="➕ Добавить обязанности мастера", callback_data=f"spec_add_master:{employee_id}")
        role_text = "Администратор"
    elif employee.role == "master_and_admin":
        # Для совмещенной роли
        builder.button(text="✏️ Изменить текст специализации", callback_data=f"spec_change_text:{employee_id}")
        builder.button(text="👔 Убрать функции мастера", callback_data=f"spec_remove_master:{employee_id}")
        builder.button(text="🔧 Убрать функции администратора", callback_data=f"spec_remove_admin:{employee_id}")
        role_text = "Администратор + Мастер"
    else:
        # Для обычного мастера
        builder.button(text="✏️ Изменить текст специализации", callback_data=f"spec_change_text:{employee_id}")
        builder.button(text="👔 Стать администратором", callback_data=f"spec_become_admin:{employee_id}")
        builder.button(text="➕ Добавить функции администратора", callback_data=f"spec_add_admin:{employee_id}")
        role_text = "Мастер"

    builder.button(text="◀️ Отмена", callback_data=f"edit_employee:{employee_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"<b>Редактирование специализации</b>\n\n"
        f"Текущая роль: {role_text}\n"
        f"Текущая специализация: {employee.specialization or 'не указана'}\n\n"
        f"Выберите действие:",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.message(EmployeeEditStates.editing_specialization)
async def edit_employee_spec_save(message: Message, state: FSMContext):
    """Сохранение новой специализации сотрудника"""
    data = await state.get_data()
    employee_id = data['employee_id']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.specialization = message.text
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    # Определяем наличие функций мастера
    has_master_functions = employee.role in ("master", "master_and_admin")
    interval_text = f"📅 Интервал записи: {employee.booking_interval_minutes} мин\n" if has_master_functions else ""

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
💼 Специализация: {employee.specialization}
{interval_text}{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Специализация успешно изменена на: <b>{message.text}</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    if has_master_functions:
        builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("spec_change_text:"))
async def spec_change_text_start(callback: CallbackQuery, state: FSMContext):
    """Изменение только текста специализации (без смены роли)"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id)

    await callback.message.edit_text("Введите новую специализацию сотрудника:")
    await state.set_state(EmployeeEditStates.editing_specialization)
    await callback.answer()


@router.callback_query(F.data.startswith("spec_become_master:"))
async def spec_become_master_start(callback: CallbackQuery, state: FSMContext):
    """Переход из администратора в мастера (потеря административных функций)"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id, new_role="master")

    await callback.message.edit_text(
        "Введите специализацию мастера (например: Парикмахер, Косметолог, Массажист):\n\n"
        "⚠️ <i>Сотрудник перестанет быть администратором и станет только мастером.</i>",
        parse_mode="HTML"
    )
    await state.set_state(EmployeeEditStates.entering_master_specialization)
    await callback.answer()


@router.callback_query(F.data.startswith("spec_add_master:"))
async def spec_add_master_start(callback: CallbackQuery, state: FSMContext):
    """Добавление функций мастера к администратору (совмещение ролей)"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id, new_role="master_and_admin")

    await callback.message.edit_text(
        "Введите специализацию мастера (например: Парикмахер, Косметолог, Массажист):\n\n"
        "ℹ️ <i>Сотрудник сохранит права администратора и сможет вести записи как мастер.</i>",
        parse_mode="HTML"
    )
    await state.set_state(EmployeeEditStates.entering_master_specialization)
    await callback.answer()


@router.message(EmployeeEditStates.entering_master_specialization)
async def save_master_specialization(message: Message, state: FSMContext):
    """Сохранение специализации и изменение роли сотрудника"""
    data = await state.get_data()
    employee_id = data['employee_id']
    new_role = data['new_role']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        old_role = employee.role
        employee.role = new_role
        employee.specialization = message.text

        # Если не был установлен интервал записи, устанавливаем значение по умолчанию
        if employee.booking_interval_minutes is None:
            business = await get_business_by_id(session, employee.business_id)
            employee.booking_interval_minutes = business.booking_interval_minutes or 30

        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    # Определяем текст о роли
    if new_role == "master":
        role_text = "Мастер"
        role_change_text = "Сотрудник теперь работает как мастер и может вести записи."
    else:  # master_and_admin
        role_text = "Администратор + Мастер"
        role_change_text = "Сотрудник совмещает обязанности администратора и мастера."

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
🎭 Роль: {role_text}
💼 Специализация: {employee.specialization}
📅 Интервал записи: {employee.booking_interval_minutes} мин
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ {role_change_text}

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("spec_remove_master:"))
async def spec_remove_master(callback: CallbackQuery):
    """Убрать функции мастера (master_and_admin → administrator)"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.role = "administrator"
        employee.specialization = "Администратор"
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
🎭 Роль: Администратор
💼 Специализация: {employee.specialization}
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Функции мастера убраны. Сотрудник теперь только администратор.

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("spec_remove_admin:"))
async def spec_remove_admin(callback: CallbackQuery):
    """Убрать функции администратора (master_and_admin → master)"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.role = "master"
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
💼 Специализация: {employee.specialization}
📅 Интервал записи: {employee.booking_interval_minutes} мин
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Функции администратора убраны. Сотрудник теперь только мастер.

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("spec_become_admin:"))
async def spec_become_admin(callback: CallbackQuery):
    """Мастер становится администратором (master → administrator)"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.role = "administrator"
        employee.specialization = "Администратор"
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
🎭 Роль: Администратор
💼 Специализация: {employee.specialization}
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Сотрудник теперь администратор. Функции мастера убраны.

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("spec_add_admin:"))
async def spec_add_admin(callback: CallbackQuery):
    """Мастер добавляет функции администратора (master → master_and_admin)"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.role = "master_and_admin"
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
🎭 Роль: Администратор + Мастер
💼 Специализация: {employee.specialization}
📅 Интервал записи: {employee.booking_interval_minutes} мин
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Добавлены функции администратора. Сотрудник совмещает обе роли.

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("edit_employee_interval:"))
async def edit_employee_interval_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования интервала сотрудника"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id)

    await callback.message.edit_text("Введите новый интервал записи (в минутах):")
    await state.set_state(EmployeeEditStates.editing_interval)
    await callback.answer()


@router.message(EmployeeEditStates.editing_interval)
async def edit_employee_interval_save(message: Message, state: FSMContext):
    """Сохранение нового интервала сотрудника"""
    data = await state.get_data()
    employee_id = data['employee_id']

    try:
        interval = int(message.text)
        if interval <= 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Ошибка! Введите корректное число минут (например: 30, 60)")
        return

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.booking_interval_minutes = interval
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
💼 Специализация: {employee.specialization}
📅 Интервал записи: {employee.booking_interval_minutes} мин
{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

✅ Интервал записи успешно изменён на: <b>{interval} мин</b>

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data.startswith("edit_employee_telegram:"))
async def edit_employee_telegram_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования Telegram ID сотрудника"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(employee_id=employee_id)

    await callback.message.edit_text(
        "Введите новый Telegram ID сотрудника:\n\n"
        "🔍 Узнать ID можно у бота @userinfobot\n"
        "Или '-' чтобы удалить (мастер не сможет войти в профиль):"
    )
    await state.set_state(EmployeeEditStates.editing_telegram_id)
    await callback.answer()


@router.message(EmployeeEditStates.editing_telegram_id)
async def edit_employee_telegram_save(message: Message, state: FSMContext):
    """Сохранение нового Telegram ID сотрудника"""
    data = await state.get_data()
    employee_id = data['employee_id']

    telegram_id = None
    if message.text != "-":
        try:
            telegram_id = int(message.text)
            if telegram_id <= 0:
                await message.answer(
                    "❌ Telegram ID должен быть положительным числом!\n"
                    "Попробуйте снова или '-' чтобы удалить:"
                )
                return
        except ValueError:
            await message.answer(
                "❌ Введите корректный Telegram ID (число) или '-' чтобы удалить:"
            )
            return

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.telegram_id = telegram_id
        await session.commit()
        await session.refresh(employee)

        business = await get_business_by_id(session, employee.business_id)

    success_msg = f"✅ Telegram ID успешно изменён на: <b>{telegram_id}</b>" if telegram_id else "✅ Telegram ID удалён"
    if not telegram_id:
        success_msg += "\n⚠️ Сотрудник не сможет войти в профиль"

    # Определяем наличие функций мастера
    has_master_functions = employee.role in ("master", "master_and_admin")
    interval_text = f"📅 Интервал записи: {employee.booking_interval_minutes} мин\n" if has_master_functions else ""

    info_text = f"""
👤 <b>{employee.name}</b>

🏢 Бизнес: {business.name}
💼 Специализация: {employee.specialization}
{interval_text}{f'📱 Telegram ID: {employee.telegram_id}' if employee.telegram_id else ''}

{success_msg}

Выберите действие:
"""

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Редактировать имя", callback_data=f"edit_employee_name:{employee_id}")
    builder.button(text="💼 Редактировать специализацию", callback_data=f"edit_employee_spec:{employee_id}")
    builder.button(text="📱 Редактировать Telegram ID", callback_data=f"edit_employee_telegram:{employee_id}")
    if has_master_functions:
        builder.button(text="📅 Редактировать интервал", callback_data=f"edit_employee_interval:{employee_id}")
    builder.button(text="🗑 Удалить сотрудника", callback_data=f"delete_employee:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{employee.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(info_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await state.clear()


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    """Пустой обработчик для неактивных кнопок (например, номер страницы)"""
    await callback.answer()


@router.callback_query(F.data == "back_to_bookings_list")
async def back_to_bookings_list(callback: CallbackQuery):
    """Возврат к выбору бизнеса для просмотра записей"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

        if not businesses:
            await callback.answer("У вас нет бизнесов", show_alert=True)
            return

    await callback.message.edit_text(
        "Выберите бизнес для просмотра записей:",
        reply_markup=admin_kb.get_businesses_admin_keyboard(businesses, "view_bookings")
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_bookings:"))
async def view_bookings(callback: CallbackQuery):
    """Просмотр записей бизнеса с выбором категории"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    category = parts[2] if len(parts) > 2 else "upcoming"
    page = int(parts[3]) if len(parts) > 3 else 0

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        all_bookings = await get_business_bookings(session, business_id)

    from datetime import datetime, date as dt_date
    now = datetime.now()
    today = now.date()

    # Фильтруем записи по категориям
    upcoming = []  # Предстоящие (не сегодня)
    today_bookings = []  # Сегодня
    cancelled = []  # Отмененные
    completed = []  # Завершенные

    for booking in all_bookings:
        booking_datetime = datetime.combine(booking.booking_date, booking.booking_time)

        if booking.status == 'cancelled':
            cancelled.append(booking)
        elif booking.status == 'completed':
            completed.append(booking)
        elif booking.booking_date == today:
            today_bookings.append(booking)
        elif booking_datetime >= now:
            upcoming.append(booking)
        else:
            # Прошедшие, но не завершенные - считаем завершенными
            completed.append(booking)

    # Выбираем нужную категорию
    category_map = {
        "upcoming": ("📅 Предстоящие", upcoming),
        "today": ("🔥 Сегодня", today_bookings),
        "cancelled": ("❌ Отмененные", cancelled),
        "completed": ("✅ Завершенные", completed)
    }

    category_name, bookings = category_map.get(category, ("📅 Предстоящие", upcoming))

    # Пагинация
    per_page = 10
    total_pages = (len(bookings) + per_page - 1) // per_page if bookings else 1
    page = max(0, min(page, total_pages - 1))

    start_idx = page * per_page
    end_idx = start_idx + per_page
    page_bookings = bookings[start_idx:end_idx]

    # Формируем текст
    info_text = f"""
🏢 <b>{business.name}</b>
{category_name} записи

"""

    if not bookings:
        info_text += "Записей в этой категории нет"
    else:
        info_text += f"Показаны {start_idx + 1}-{min(end_idx, len(bookings))} из {len(bookings)}\n"

    # Создаем клавиатуру
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    # Кнопки категорий
    categories = [
        ("📅", "upcoming", len(upcoming)),
        ("🔥", "today", len(today_bookings)),
        ("❌", "cancelled", len(cancelled)),
        ("✅", "completed", len(completed))
    ]

    for emoji, cat, count in categories:
        btn_text = f"{emoji} {count}"
        if cat == category:
            btn_text = f"• {btn_text} •"
        builder.button(text=btn_text, callback_data=f"view_bookings:{business_id}:{cat}:0")

    builder.adjust(4)

    # Записи
    if page_bookings:
        for booking in page_bookings:
            status_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "cancelled": "❌",
                "completed": "✔️"
            }.get(booking.status, "❓")

            builder.button(
                text=f"{status_emoji} {format_date_with_weekday(booking.booking_date, short=True)} {booking.booking_time.strftime('%H:%M')} - {booking.client.full_name[:20]}",
                callback_data=f"admin_booking_detail:{booking.id}"
            )

        builder.adjust(1)

    # Пагинация
    if total_pages > 1:
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(("◀️ Назад", f"view_bookings:{business_id}:{category}:{page-1}"))

        pagination_buttons.append((f"{page + 1}/{total_pages}", "noop"))

        if page < total_pages - 1:
            pagination_buttons.append(("Вперед ▶️", f"view_bookings:{business_id}:{category}:{page+1}"))

        for text, data in pagination_buttons:
            builder.button(text=text, callback_data=data)

        builder.adjust(len(pagination_buttons))

    # Навигация
    builder.button(text="◀️ Выбор бизнеса", callback_data="back_to_bookings_list")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(2)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_booking_detail:"))
async def admin_booking_detail(callback: CallbackQuery):
    """Детальный просмотр записи с действиями"""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись со всеми связями
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.employee)
            )
            .where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()

        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        # Проверка прав доступа
        from database import is_business_owner
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return

        # Получаем статистику клиента
        result = await session.execute(
            select(func.count(Booking.id))
            .where(Booking.client_id == booking.client_id)
            .where(Booking.business_id == booking.business_id)
        )
        total_bookings = result.scalar() or 0

        result = await session.execute(
            select(func.count(Booking.id))
            .where(Booking.client_id == booking.client_id)
            .where(Booking.business_id == booking.business_id)
            .where(Booking.status == "completed")
        )
        completed_bookings = result.scalar() or 0

    # Формируем сообщение
    status_emoji = {
        "pending": "⏳ Ожидает",
        "confirmed": "✅ Подтверждена",
        "completed": "✔️ Завершена",
        "cancelled": "❌ Отменена"
    }.get(booking.status, "❓ Неизвестно")

    info_text = f"""
📋 <b>Детали записи</b>

📅 Дата: {format_date_with_weekday(booking.booking_date)}
🕐 Время: {booking.booking_time.strftime('%H:%M')}
📊 Статус: {status_emoji}

🏢 {booking.business.name}
💼 Услуга: {booking.service.name}
⏱ Длительность: {booking.service.duration_minutes} мин
💰 Цена: {booking.service.price} ₽
"""

    if booking.employee:
        info_text += f"👔 Мастер: {booking.employee.name}\n"

    info_text += f"""
👤 <b>Клиент: {booking.client.full_name}</b>
"""

    if booking.client.phone:
        info_text += f"📱 Телефон: {format_phone(booking.client.phone)}\n"

    info_text += f"""
📈 Всего записей: {total_bookings}
✔️ Завершено: {completed_bookings}
"""

    if booking.notes:
        info_text += f"\n💬 Комментарий: {booking.notes}"

    # Формируем клавиатуру с действиями
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    if booking.status == "pending":
        builder.button(text="✅ Подтвердить", callback_data=f"admin_confirm_booking:{booking_id}", style="success")
        builder.button(text="❌ Отменить", callback_data=f"admin_cancel_booking:{booking_id}", style="danger")
    elif booking.status in ("confirmed", "prepayment_confirmed"):
        builder.button(text="✔️ Завершить (освободить время)", callback_data=f"admin_complete_booking:{booking_id}", style="success")
        builder.button(text="❌ Отменить", callback_data=f"admin_cancel_booking:{booking_id}", style="danger")

    if booking.status not in ("cancelled", "completed") and booking.duration_minutes is None:
        builder.button(text="🔁 Сменить услугу", callback_data=f"admin_chsvc:{booking_id}")
    builder.button(text="📊 История клиента", callback_data=f"client_history:{booking.client_id}:{booking.business_id}")
    builder.button(text="◀️ Назад к записям", callback_data=f"view_bookings:{booking.business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_booking:"))
async def admin_confirm_booking(callback: CallbackQuery):
    """Подтверждение записи администратором"""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.employee),
            )
            .where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()

        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        # Проверка прав доступа
        from database import is_business_owner
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return

        if booking.status != "pending":
            await callback.answer("❌ Можно подтвердить только записи в статусе 'Ожидает'", show_alert=True)
            return

        # Подтверждаем запись
        booking.status = "confirmed"
        await session.commit()

        try:
            from bot.services.email_notifications import send_booking_email
            await send_booking_email([booking], "confirmed")
        except Exception as email_err:
            logger.warning("Email confirmation failed for booking %s: %s", booking.id, email_err)

    await callback.answer("✅ Запись подтверждена")
    # Перенаправляем обратно на детали записи
    await admin_booking_detail(callback)


@router.callback_query(F.data.startswith("admin_complete_booking:"))
async def admin_complete_booking(callback: CallbackQuery):
    """Завершение записи администратором"""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.business)
            )
            .where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()

        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        # Проверка прав доступа
        from database import is_business_owner
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return

        if booking.status not in ["pending", "confirmed", "prepayment_confirmed"]:
            await callback.answer("❌ Можно завершить только активные записи", show_alert=True)
            return

        # Завершаем запись; момент нужен, чтобы в расписании время освободилось с него
        from datetime_utils import utc_now
        booking.status = "completed"
        booking.finished_at = utc_now()
        await session.commit()

    await callback.answer("✔️ Запись завершена")
    # Перенаправляем обратно на детали записи
    await admin_booking_detail(callback)


@router.callback_query(F.data.startswith("admin_chsvc:"))
async def admin_change_service_menu(callback: CallbackQuery):
    """Список услуг, на которые можно заменить услугу в записи"""
    booking_id = int(callback.data.split(":")[1])
    from database import is_business_owner
    from database.models import Service as ServiceModel
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    async with async_session_maker() as session:
        booking = await session.get(Booking, booking_id)
        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return
        user = await get_or_create_user(
            session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return
        current = await session.get(ServiceModel, booking.service_id)
        services = (await session.execute(
            select(ServiceModel).where(
                ServiceModel.business_id == booking.business_id,
                ServiceModel.is_active.is_(True),
                ServiceModel.id != booking.service_id,
                ServiceModel.is_additional.is_(bool(current and current.is_additional)),
            ).order_by(ServiceModel.sort_order, ServiceModel.id)
        )).scalars().all()
    builder = InlineKeyboardBuilder()
    for sv in services:
        builder.button(text=f"{sv.name} · {sv.duration_minutes} мин · {sv.price} ₽", callback_data=f"admin_setsvc:{booking_id}:{sv.id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_booking_detail:{booking_id}")
    builder.adjust(1)
    await callback.message.edit_text(
        "🔁 <b>На какую услугу заменить?</b>\nИспользуйте, если клиент выбрал не ту.",
        parse_mode="HTML", reply_markup=builder.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_setsvc:"))
async def admin_change_service_apply(callback: CallbackQuery):
    """Заменить услугу в записи"""
    try:
        _, booking_id, service_id = callback.data.split(":")
        booking_id, service_id = int(booking_id), int(service_id)
    except ValueError:
        await callback.answer("❌ Ошибка данных", show_alert=True)
        return
    from database import is_business_owner
    from database.booking_changes import BookingChangeError, change_booking_service
    async with async_session_maker() as session:
        booking = (await session.execute(
            select(Booking).options(selectinload(Booking.service)).where(Booking.id == booking_id).with_for_update()
        )).scalar_one_or_none()
        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return
        user = await get_or_create_user(
            session, callback.from_user.id, callback.from_user.username, callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return
        try:
            await change_booking_service(session, booking, service_id)
            await session.commit()
        except BookingChangeError as exc:
            await session.rollback()
            await callback.answer(f"❌ {exc.detail}", show_alert=True)
            return
    await callback.answer("✅ Услуга заменена")
    await admin_booking_detail(callback)


@router.callback_query(F.data.startswith("admin_cancel_booking:"))
async def admin_cancel_booking(callback: CallbackQuery):
    """Отмена записи администратором"""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Получаем запись со всеми связями для уведомления
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.business),
                selectinload(Booking.service)
            )
            .where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()

        if not booking:
            await callback.answer("❌ Запись не найдена", show_alert=True)
            return

        # Проверка прав доступа
        from database import is_business_owner
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, booking.business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этой записью", show_alert=True)
            return

        if booking.status not in ["pending", "confirmed"]:
            await callback.answer("❌ Можно отменить только активные записи", show_alert=True)
            return

        # Отменяем запись
        booking.status = "cancelled"
        await session.commit()

        try:
            from bot.services.email_notifications import send_booking_email
            await send_booking_email([booking], "cancelled")
        except Exception as email_err:
            logger.warning("Email cancellation failed for booking %s: %s", booking.id, email_err)

        # Уведомляем клиента об отмене
        try:
            await callback.bot.send_message(
                chat_id=booking.client.telegram_id,
                text=f"""
❌ <b>Ваша запись отменена</b>

📅 Дата: {format_date_with_weekday(booking.booking_date)}
🕐 Время: {booking.booking_time.strftime('%H:%M')}
🏢 {booking.business.name}
💼 {booking.service.name}

Приносим извинения за неудобства.
""",
                parse_mode="HTML"
            )
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error notifying client about cancellation: {e}")

        # Уведомляем совладельцев бизнеса (владелец, кликнувший отмену,
        # получит уведомление и сам — приемлемо, зато сохладельцы теперь
        # тоже узнают об отмене, чего раньше не происходило вообще)
        try:
            from bot.services.notifications import notify_all_owners_cancelled_booking
            await notify_all_owners_cancelled_booking(callback.bot, booking)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error notifying co-owners about cancellation: {e}")

    await callback.answer("❌ Запись отменена, клиент уведомлен")
    # Перенаправляем обратно на детали записи
    await admin_booking_detail(callback)


@router.callback_query(F.data.startswith("client_history:"))
async def view_client_history(callback: CallbackQuery):
    """Просмотр полной истории клиента в бизнесе"""
    parts = callback.data.split(":")
    client_id = int(parts[1])
    business_id = int(parts[2])

    async with async_session_maker() as session:
        # Проверка прав доступа
        from database import is_business_owner
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для просмотра этой информации", show_alert=True)
            return

        # Получаем клиента
        from database import User
        result = await session.execute(
            select(User).where(User.id == client_id)
        )
        client = result.scalar_one_or_none()

        if not client:
            await callback.answer("❌ Клиент не найден", show_alert=True)
            return

        # Получаем бизнес
        business = await get_business_by_id(session, business_id)

        # Получаем все записи клиента в этом бизнесе
        from database import Service
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.service),
                selectinload(Booking.employee)
            )
            .where(Booking.client_id == client_id)
            .where(Booking.business_id == business_id)
            .order_by(Booking.booking_date.desc(), Booking.booking_time.desc())
        )
        all_bookings = result.scalars().all()

        # Статистика
        total_bookings = len(all_bookings)
        completed = len([b for b in all_bookings if b.status == "completed"])
        cancelled = len([b for b in all_bookings if b.status == "cancelled"])
        pending = len([b for b in all_bookings if b.status in ["pending", "confirmed"]])

        # Общая сумма (только завершенные)
        total_spent = sum(b.service.price for b in all_bookings if b.status == "completed")

        # Первый визит
        first_booking = all_bookings[-1] if all_bookings else None

        # Последний завершенный визит
        completed_bookings = [b for b in all_bookings if b.status == "completed"]
        last_visit = completed_bookings[0] if completed_bookings else None

        # Любимые услуги (топ 3)
        from collections import Counter
        service_counts = Counter()
        for booking in all_bookings:
            if booking.status == "completed":
                service_counts[booking.service.name] += 1

        top_services = service_counts.most_common(3)

    # Формируем сообщение
    info_text = f"""
👤 <b>История клиента</b>

<b>{client.full_name}</b>
"""

    if client.phone:
        info_text += f"📱 {format_phone(client.phone)}\n"

    info_text += f"""
🏢 Бизнес: {business.name}

📊 <b>Статистика:</b>
📈 Всего записей: {total_bookings}
✔️ Завершено: {completed}
❌ Отменено: {cancelled}
⏳ Активных: {pending}
💰 Потрачено: {total_spent} ₽
"""

    if first_booking:
        info_text += f"\n🎯 Первый визит: {format_date_with_weekday(first_booking.booking_date)}"

    if last_visit:
        info_text += f"\n🕐 Последний визит: {format_date_with_weekday(last_visit.booking_date)}"

    # Любимые услуги
    if top_services:
        info_text += "\n\n⭐ <b>Любимые услуги:</b>\n"
        for i, (service_name, count) in enumerate(top_services, 1):
            info_text += f"{i}. {service_name} ({count} раз)\n"

    # Последние визиты
    if all_bookings:
        info_text += "\n📅 <b>Последние записи:</b>\n"
        for booking in all_bookings[:5]:
            status_emoji = {
                "pending": "⏳",
                "confirmed": "✅",
                "completed": "✔️",
                "cancelled": "❌"
            }.get(booking.status, "❓")

            date_str = format_date_with_weekday(booking.booking_date)
            time_str = booking.booking_time.strftime('%H:%M')

            info_text += f"{status_emoji} {date_str} {time_str} - {booking.service.name}"
            if booking.employee:
                info_text += f" ({booking.employee.name})"
            info_text += "\n"

    # Клавиатура
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    builder.button(text="◀️ Назад", callback_data=f"view_bookings:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(
        info_text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ===== РЕДАКТИРОВАНИЕ БИЗНЕСА =====

@router.callback_query(F.data.startswith("edit_business:"))
async def start_business_edit(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования бизнеса"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав доступа
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав для управления этим бизнесом", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

    await state.update_data(business_id=business_id)

    info_text = f"""
✏️ <b>Редактирование: {business.name}</b>

Выберите поле для редактирования:
"""

    keyboard = admin_kb.get_business_edit_keyboard(business_id)

    try:
        await callback.message.edit_text(
            info_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    except Exception:
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer(
            info_text,
            parse_mode="HTML",
            reply_markup=keyboard
        )
    await state.set_state(BusinessEditStates.choosing_field)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:name:"), BusinessEditStates.choosing_field)
async def edit_name_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования названия"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_name = business.name

    await callback.message.edit_text(
        f"📝 <b>Текущее название:</b>\n<code>{current_name}</code>\n\n"
        "Введите новое название бизнеса:",
        parse_mode="HTML"
    )
    await state.set_state(BusinessEditStates.editing_name)
    await callback.answer()


@router.message(BusinessEditStates.editing_name)
async def edit_name_save(message: Message, state: FSMContext):
    """Сохранение нового названия"""
    data = await state.get_data()
    business_id = data['business_id']

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.name = message.text
        await session.commit()
        await session.refresh(business)

    info_text = f"""
✏️ <b>Редактирование: {message.text}</b>

✅ Название успешно изменено на: <b>{message.text}</b>

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:description:"), BusinessEditStates.choosing_field)
async def edit_description_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования описания"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_desc = business.description

    if current_desc:
        text = (
            f"📄 <b>Текущее описание:</b>\n<code>{current_desc}</code>\n\n"
            "Введите новое описание (или '-' для удаления):"
        )
    else:
        text = "📄 Описание не задано.\n\nВведите новое описание (или '-' для удаления):"

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(BusinessEditStates.editing_description)
    await callback.answer()


@router.message(BusinessEditStates.editing_description)
async def edit_description_save(message: Message, state: FSMContext):
    """Сохранение нового описания"""
    data = await state.get_data()
    business_id = data['business_id']

    description = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.description = description
        await session.commit()
        await session.refresh(business)

        business_name = business.name

    description_display = description if description else "(удалено)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Описание успешно обновлено!
📄 Новое описание: <i>{description_display}</i>

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:address:"), BusinessEditStates.choosing_field)
async def edit_address_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования адреса"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_address = business.address

    if current_address:
        text = (
            f"📍 <b>Текущий адрес:</b>\n<code>{current_address}</code>\n\n"
            "Введите новый адрес (или '-' для удаления):"
        )
    else:
        text = "📍 Адрес не задан.\n\nВведите новый адрес (или '-' для удаления):"

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(BusinessEditStates.editing_address)
    await callback.answer()


@router.message(BusinessEditStates.editing_address)
async def edit_address_save(message: Message, state: FSMContext):
    """Сохранение нового адреса"""
    data = await state.get_data()
    business_id = data['business_id']

    address = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.address = address
        await session.commit()
        await session.refresh(business)

        business_name = business.name

    address_display = address if address else "(удалён)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Адрес успешно обновлен!
📍 Новый адрес: <i>{address_display}</i>

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:phone:"), BusinessEditStates.choosing_field)
async def edit_phone_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования телефона"""
    from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    # Создаём клавиатуру с кнопкой отправки контакта
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="+79991234567"
    )

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_phone = business.phone

    if current_phone:
        await callback.message.edit_text(
            f"📞 <b>Текущий телефон:</b>\n<code>{current_phone}</code>\n\n"
            "Введите новый телефон вручную или используйте кнопку ниже:",
            parse_mode="HTML"
        )
    else:
        await callback.message.edit_text("📞 Телефон не задан.\n\nВведите новый телефон вручную или используйте кнопку ниже:")

    await callback.message.answer(
        "Вы можете:\n• Отправить свой контакт кнопкой\n• Ввести номер вручную (например: +79991234567)\n• Написать '-' для удаления телефона",
        reply_markup=keyboard
    )
    await state.set_state(BusinessEditStates.editing_phone)
    await callback.answer()


@router.message(BusinessEditStates.editing_phone)
async def edit_phone_save(message: Message, state: FSMContext):
    """Сохранение нового телефона"""
    from bot.utils.validators import validate_phone
    from aiogram.types import ReplyKeyboardRemove

    data = await state.get_data()
    business_id = data['business_id']

    # Обработка отмены
    if message.text == "❌ Отмена":
        async with async_session_maker() as session:
            business = await get_business_by_id(session, business_id)
            business_name = business.name

        info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

❌ Изменение телефона отменено

Выберите поле для редактирования:
"""
        await message.answer(
            info_text,
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove()
        )
        await message.answer(
            "Выберите действие:",
            reply_markup=admin_kb.get_business_edit_keyboard(business_id)
        )
        await state.set_state(BusinessEditStates.choosing_field)
        return

    phone = None

    # Обработка контакта (если пользователь отправил свой контакт)
    if message.contact:
        phone = format_phone(message.contact.phone_number)
    elif message.text != "-":
        if not validate_phone(message.text):
            await message.answer(
                "❌ Неверный формат телефона!\n"
                "Используйте формат: +7XXXXXXXXXX или 8XXXXXXXXXX\n\n"
                "Попробуйте снова или '-' чтобы очистить:"
            )
            return
        # Форматируем телефон: если 10 цифр - добавляем +7
        phone = format_phone(message.text)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.phone = phone
        await session.commit()
        await session.refresh(business)

        business_name = business.name

    phone_display = phone if phone else "(удалён)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Телефон успешно обновлен!
📞 Новый телефон: <code>{phone_display}</code>

Выберите поле для редактирования:
"""

    # Убираем reply клавиатуру и показываем inline клавиатуру
    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove()
    )
    await message.answer(
        "Выберите действие:",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:hours:"), BusinessEditStates.choosing_field)
async def edit_hours_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования времени работы"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if business.working_hours_start and business.working_hours_end:
            current_hours = f"{business.working_hours_start.strftime('%H:%M')}-{business.working_hours_end.strftime('%H:%M')}"
        else:
            current_hours = None

    # Добавляем inline кнопку отмены
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отменить", callback_data=f"cancel_edit:{business_id}")
    builder.adjust(1)

    if current_hours:
        text = (
            f"⏰ <b>Текущее время работы:</b>\n<code>{current_hours}</code>\n\n"
            "Введите новое время работы в формате:\n"
            "<b>09:00-18:00</b>\n\n"
            "Или '-' для удаления:"
        )
    else:
        text = (
            "⏰ Время работы не задано.\n\n"
            "Введите новое время работы в формате:\n"
            "<b>09:00-18:00</b>\n\n"
            "Или '-' для удаления:"
        )

    await callback.message.edit_text(
        text,
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )
    await state.set_state(BusinessEditStates.editing_working_hours)
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_edit:"))
async def cancel_edit_field(callback: CallbackQuery, state: FSMContext):
    """Отмена редактирования поля"""
    business_id = int(callback.data.split(":")[1])

    await state.clear()

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    await callback.message.edit_text(
        f"❌ Редактирование отменено.\n\n"
        f"Выберите поле для редактирования:",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)
    await callback.answer()


@router.message(BusinessEditStates.editing_working_hours)
async def edit_hours_save(message: Message, state: FSMContext):
    """Сохранение нового времени работы"""
    from datetime import time

    data = await state.get_data()
    business_id = data['business_id']

    working_hours_start = None
    working_hours_end = None

    if message.text != "-":
        try:
            times = message.text.split("-")
            if len(times) == 2:
                # Заменяем точки на двоеточия для поддержки формата "9.30-18.00"
                start_time = times[0].strip().replace(".", ":")
                end_time = times[1].strip().replace(".", ":")

                start_hour, start_min = map(int, start_time.split(":"))
                end_hour, end_min = map(int, end_time.split(":"))
                working_hours_start = time(start_hour, start_min)
                working_hours_end = time(end_hour, end_min)
        except (ValueError, IndexError):
            await message.answer(
                "❌ Неверный формат времени!\n"
                "Используйте формат: 09:00-18:00 или 9.30-18.00"
            )
            return

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.working_hours_start = working_hours_start
        business.working_hours_end = working_hours_end
        await session.commit()
        await session.refresh(business)

        business_name = business.name

    time_display = f"{working_hours_start.strftime('%H:%M')}-{working_hours_end.strftime('%H:%M')}" if working_hours_start else "(не указано)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Время работы успешно обновлено!
⏰ Новое время: <b>{time_display}</b>

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:break:"), BusinessEditStates.choosing_field)
async def edit_break_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования перерыва"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if business.break_start and business.break_end:
            current_break = f"{business.break_start.strftime('%H:%M')}-{business.break_end.strftime('%H:%M')}"
        else:
            current_break = None

    if current_break:
        text = (
            f"☕ <b>Текущий перерыв:</b>\n<code>{current_break}</code>\n\n"
            "Введите новое время перерыва в формате:\n"
            "<b>13:00-14:00</b> или <b>13.00-14.00</b>\n\n"
            "Или '-' для удаления перерыва:"
        )
    else:
        text = (
            "☕ Перерыв не задан.\n\n"
            "Введите новое время перерыва в формате:\n"
            "<b>13:00-14:00</b> или <b>13.00-14.00</b>\n\n"
            "Или '-' для удаления перерыва:"
        )

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(BusinessEditStates.editing_break)
    await callback.answer()


@router.message(BusinessEditStates.editing_break)
async def edit_break_save(message: Message, state: FSMContext):
    """Сохранение нового времени перерыва"""
    from datetime import time

    data = await state.get_data()
    business_id = data['business_id']

    break_start = None
    break_end = None

    if message.text != "-":
        try:
            times = message.text.split("-")
            if len(times) == 2:
                # Заменяем точки на двоеточия
                start_time = times[0].strip().replace(".", ":")
                end_time = times[1].strip().replace(".", ":")

                start_hour, start_min = map(int, start_time.split(":"))
                end_hour, end_min = map(int, end_time.split(":"))
                break_start = time(start_hour, start_min)
                break_end = time(end_hour, end_min)
        except (ValueError, IndexError):
            await message.answer(
                "❌ Неверный формат времени!\n"
                "Используйте формат: 13:00-14:00 или 13.00-14.00"
            )
            return

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.break_start = break_start
        business.break_end = break_end
        await session.commit()
        await session.refresh(business)

        business_name = business.name

    break_display = f"{break_start.strftime('%H:%M')}-{break_end.strftime('%H:%M')}" if break_start else "(не указан)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Перерыв успешно обновлен!
☕ Новый перерыв: <b>{break_display}</b>

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


@router.callback_query(F.data.startswith("edit_field:photos:"), BusinessEditStates.choosing_field)
async def edit_photos_start(callback: CallbackQuery, state: FSMContext):
    """Начало управления фотографиями"""
    import json

    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        photos = json.loads(business.photos) if business.photos else []
        business_name = business.name

    # Отправляем текущие фото если есть
    if photos:
        await callback.message.answer(f"📷 <b>Текущие фотографии {business_name}:</b>", parse_mode="HTML")
        for photo_id in photos:
            try:
                await callback.message.answer_photo(photo_id)
            except Exception:
                pass

    has_photos = len(photos) > 0

    await callback.message.answer(
        f"📷 <b>Управление фотографиями: {business_name}</b>\n\n"
        f"Фотографий: {len(photos)}\n\n"
        "Выберите действие:",
        parse_mode="HTML",
        reply_markup=admin_kb.get_photos_management_keyboard(business_id, has_photos)
    )
    await state.set_state(BusinessEditStates.editing_photos)
    await callback.answer()


@router.callback_query(F.data.startswith("add_photo:"), BusinessEditStates.editing_photos)
async def add_photo_prompt(callback: CallbackQuery, state: FSMContext):
    """Запрос на добавление фото"""
    business_id = int(callback.data.split(":")[1])
    await state.update_data(business_id=business_id)

    await callback.message.edit_text(
        "📷 Отправьте фотографию бизнеса:\n\n"
        "Отправьте фото или '-' для отмены",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BusinessEditStates.editing_photos, F.photo)
async def photo_received(message: Message, state: FSMContext):
    """Получение и сохранение фото"""
    import json

    data = await state.get_data()
    business_id = data['business_id']

    # Получаем file_id самого большого фото
    photo_file_id = message.photo[-1].file_id

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        photos = json.loads(business.photos) if business.photos else []
        photos.append(photo_file_id)
        business.photos = json.dumps(photos)
        await session.commit()
        await session.refresh(business)
        business_name = business.name

    await message.answer(
        f"✅ Фотография добавлена!\n\n"
        f"Всего фотографий: {len(photos)}",
        parse_mode="HTML",
        reply_markup=admin_kb.get_photos_management_keyboard(business_id, True)
    )


@router.callback_query(F.data.startswith("delete_all_photos:"), BusinessEditStates.editing_photos)
async def delete_all_photos(callback: CallbackQuery, state: FSMContext):
    """Удаление всех фотографий"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.photos = None
        await session.commit()
        business_name = business.name

    await callback.message.edit_text(
        f"✅ Все фотографии удалены!",
        parse_mode="HTML",
        reply_markup=admin_kb.get_photos_management_keyboard(business_id, False)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back_to_edit:"), BusinessEditStates.editing_photos)
async def back_to_edit_from_photos(callback: CallbackQuery, state: FSMContext):
    """Возврат к редактированию бизнеса"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business_name = business.name

    await callback.message.edit_text(
        f"✏️ <b>Редактирование: {business_name}</b>\n\n"
        "Выберите поле для редактирования:",
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:video:"), BusinessEditStates.choosing_field)
async def edit_business_video_start(callback: CallbackQuery, state: FSMContext):
    """Начало управления видеообзором бизнеса"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        has_video = bool(business.video)
        business_name = business.name

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    if has_video:
        builder.button(text="👁 Посмотреть видео", callback_data=f"biz_view_video:{business_id}")
        builder.button(text="🔄 Заменить видео", callback_data=f"biz_set_video:{business_id}")
        builder.button(text="🗑 Удалить видео", callback_data=f"biz_del_video:{business_id}", style="danger")
    else:
        builder.button(text="📎 Загрузить видео", callback_data=f"biz_set_video:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_business:{business_id}")
    builder.adjust(1)

    status = "✅ Видео загружено" if has_video else "❌ Видео не загружено"
    await callback.message.edit_text(
        f"🎬 <b>Видеообзор: {business_name}</b>\n\n"
        f"Статус: {status}\n\n"
        "Видеообзор показывается клиентам при выборе вашего заведения.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.set_state(BusinessEditStates.editing_video)
    await callback.answer()


@router.callback_query(F.data.startswith("biz_view_video:"), BusinessEditStates.editing_video)
async def view_business_video(callback: CallbackQuery, state: FSMContext):
    """Просмотр текущего видеообзора"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if business.video:
        try:
            await callback.message.answer_video(business.video)
        except Exception:
            await callback.message.answer("❌ Не удалось отправить видео")
    else:
        await callback.answer("❌ Видео не загружено", show_alert=True)
    await callback.answer()


@router.callback_query(F.data.startswith("biz_set_video:"), BusinessEditStates.editing_video)
async def set_business_video_prompt(callback: CallbackQuery, state: FSMContext):
    """Запрос на загрузку видео"""
    business_id = int(callback.data.split(":")[1])
    await state.update_data(business_id=business_id)

    await callback.message.edit_text(
        "🎬 Отправьте видео для обзора бизнеса:\n\n"
        "📎 Максимальный размер: 50 МБ\n"
        "Отправьте видео или напишите '-' для отмены",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(BusinessEditStates.editing_video, F.video)
async def business_video_received(message: Message, state: FSMContext):
    """Получение и сохранение видеообзора"""
    max_size_mb = 100
    if message.video.file_size and message.video.file_size > max_size_mb * 1024 * 1024:
        await message.answer(f"❌ Видео слишком большое. Максимум {max_size_mb} МБ.")
        return

    data = await state.get_data()
    business_id = data['business_id']

    video_file_id = message.video.file_id

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.video = video_file_id
        await session.commit()
        business_name = business.name

    # Скачиваем для webapp
    saved = await _save_video_to_cache(message.bot, video_file_id)
    webapp_note = "" if saved else "\n\n⚠️ Видео больше 20 МБ — в веб-приложении не отобразится, только в боте."

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="👁 Посмотреть видео", callback_data=f"biz_view_video:{business_id}")
    builder.button(text="🔄 Заменить видео", callback_data=f"biz_set_video:{business_id}")
    builder.button(text="🗑 Удалить видео", callback_data=f"biz_del_video:{business_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"edit_business:{business_id}")
    builder.adjust(1)

    await message.answer(
        f"✅ Видеообзор для <b>{business_name}</b> сохранён!{webapp_note}",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.message(BusinessEditStates.editing_video, F.text)
async def business_video_text(message: Message, state: FSMContext):
    """Обработка текста вместо видео (отмена)"""
    data = await state.get_data()
    business_id = data['business_id']

    if message.text.strip() == '-':
        async with async_session_maker() as session:
            business = await get_business_by_id(session, business_id)
            business_name = business.name

        await message.answer(
            f"✏️ <b>Редактирование: {business_name}</b>\n\n"
            "Выберите поле для редактирования:",
            parse_mode="HTML",
            reply_markup=admin_kb.get_business_edit_keyboard(business_id)
        )
        await state.set_state(BusinessEditStates.choosing_field)
    else:
        await message.answer("❌ Отправьте видеофайл или '-' для отмены")


@router.callback_query(F.data.startswith("biz_del_video:"), BusinessEditStates.editing_video)
async def delete_business_video(callback: CallbackQuery, state: FSMContext):
    """Удаление видеообзора"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.video = None
        await session.commit()
        business_name = business.name

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="📎 Загрузить видео", callback_data=f"biz_set_video:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_business:{business_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"✅ Видеообзор удалён.",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("back_to_edit:"), BusinessEditStates.editing_video)
async def back_to_edit_from_video(callback: CallbackQuery, state: FSMContext):
    """Возврат к редактированию бизнеса из видео"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business_name = business.name

    await callback.message.edit_text(
        f"✏️ <b>Редактирование: {business_name}</b>\n\n"
        "Выберите поле для редактирования:",
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)
    await callback.answer()


@router.callback_query(F.data.startswith("edit_field:directions:"), BusinessEditStates.choosing_field)
async def edit_directions_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования инструкций как добраться"""
    business_id = int(callback.data.split(":")[2])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_directions = business.directions

    if current_directions:
        text = (
            f"🗺 <b>Текущие инструкции:</b>\n<code>{current_directions}</code>\n\n"
            "Введите новые инструкции как добраться:\n\n"
            "Или '-' для удаления:"
        )
    else:
        text = "🗺 Инструкции не заданы.\n\nВведите инструкции как добраться:\n\nИли '-' для удаления:"

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(BusinessEditStates.editing_directions)
    await callback.answer()


@router.message(BusinessEditStates.editing_directions)
async def edit_directions_save(message: Message, state: FSMContext):
    """Сохранение инструкций как добраться"""
    data = await state.get_data()
    business_id = data['business_id']

    directions = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.directions = directions
        await session.commit()
        await session.refresh(business)
        business_name = business.name

    directions_display = directions if directions else "(не указано)"

    info_text = f"""
✏️ <b>Редактирование: {business_name}</b>

✅ Инструкции успешно обновлены!
🗺 Как добраться:
{directions_display}

Выберите поле для редактирования:
"""

    await message.answer(
        info_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_edit_keyboard(business_id)
    )
    await state.set_state(BusinessEditStates.choosing_field)


# ===== ВОЗВРАТ В ГЛАВНОЕ МЕНЮ =====

@router.callback_query(F.data == "main_menu")
async def back_to_main_menu(callback: CallbackQuery, state: FSMContext):
    """Возврат в главное меню"""
    await state.clear()

    # Получаем роли для клавиатуры
    async with async_session_maker() as session:
        employee = await get_employee_by_telegram_id(session, callback.from_user.id)

    # Удаляем предыдущее сообщение
    try:
        await callback.message.delete()
    except Exception:
        pass

    # Отправляем новое сообщение с главным меню владельца
    await callback.bot.send_message(
        chat_id=callback.from_user.id,
        text="🏢 Панель владельца\n\nЗдесь вы можете управлять своими бизнесами, услугами, сотрудниками и просматривать записи клиентов.",
        reply_markup=admin_kb.get_admin_menu_keyboard(show_employee_button=(employee is not None), is_super_admin=(callback.from_user.id == 437862772))
    )
    await callback.answer()


# ===== НАЗНАЧЕНИЕ АДМИНИСТРАТОРА БИЗНЕСА =====

from bot.states.admin import AddAdministratorStates


@router.callback_query(F.data.startswith("add_administrator:"))
async def start_add_administrator(callback: CallbackQuery, state: FSMContext):
    """Начать процесс назначения администратора бизнеса"""
    business_id = int(callback.data.split(":")[1])

    # Проверка прав
    from database import is_business_owner, get_or_create_user
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            callback.from_user.id,
            callback.from_user.username,
            callback.from_user.full_name
        )
        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("❌ У вас нет прав", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

    await state.update_data(business_id=business_id)
    await state.set_state(AddAdministratorStates.entering_name)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"cancel_add_admin:{business_id}")

    await callback.message.edit_text(
        f"👔 <b>Назначение администратора</b>\n\n"
        f"Бизнес: {business.name}\n\n"
        f"Введите имя администратора:",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.message(AddAdministratorStates.entering_name)
async def process_administrator_name(message: Message, state: FSMContext):
    """Обработка имени администратора"""
    name = message.text.strip()
    if len(name) < 2:
        await message.answer("❌ Имя слишком короткое. Введите имя:")
        return

    data = await state.get_data()
    business_id = data.get("business_id")

    await state.update_data(admin_name=name)
    await state.set_state(AddAdministratorStates.entering_telegram_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"cancel_add_admin:{business_id}")

    sent_msg = await message.answer(
        f"👔 <b>Назначение администратора</b>\n\n"
        f"Имя: {name}\n\n"
        f"Теперь введите Telegram ID администратора.\n\n"
        f"<i>Администратор может узнать свой ID отправив /start боту - ID показывается в справке (/help)</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    # Сохраняем ID сообщения для удаления при отмене
    await state.update_data(request_message_id=sent_msg.message_id)


@router.message(AddAdministratorStates.entering_telegram_id)
async def process_administrator_telegram_id(message: Message, state: FSMContext):
    """Обработка Telegram ID администратора"""
    try:
        telegram_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Неверный формат. Введите числовой Telegram ID:")
        return

    data = await state.get_data()
    business_id = data.get("business_id")
    admin_name = data.get("admin_name")

    # Создаем администратора в БД
    from database.crud import create_employee
    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

        # Проверяем нет ли уже такого telegram_id
        existing = await get_employee_by_telegram_id(session, telegram_id)
        warning_text = ""

        if existing:
            if existing.business_id == business_id:
                # Сотрудник уже есть в этом бизнесе - обновляем его роль
                old_role = "администратор" if existing.role == "administrator" else "мастер"
                existing.role = "administrator"
                existing.name = admin_name
                existing.specialization = "Администратор"
                await session.commit()
                await session.refresh(existing)

                from aiogram.utils.keyboard import InlineKeyboardBuilder
                builder = InlineKeyboardBuilder()
                builder.button(text="👥 К сотрудникам", callback_data=f"manage_employees:{business_id}")
                builder.button(text="🏠 Главное меню", callback_data="main_menu")
                builder.adjust(1)

                await message.answer(
                    f"✅ <b>Роль сотрудника обновлена!</b>\n\n"
                    f"👔 Имя: {admin_name}\n"
                    f"🆔 Telegram ID: {telegram_id}\n"
                    f"🏢 Бизнес: {business.name}\n\n"
                    f"⚠️ Этот пользователь уже был сотрудником ({old_role}).\n"
                    f"Теперь он назначен администратором и может:\n"
                    f"• Записывать клиентов к любому мастеру\n"
                    f"• Отменять записи\n"
                    f"• Видеть статистику и клиентов",
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
                await state.clear()
                return
            else:
                # Сотрудник есть в другом бизнесе - предупреждаем
                warning_text = f"\n\n⚠️ <i>Этот пользователь также является сотрудником в '{existing.business.name}'</i>"

        # Создаем администратора
        new_admin = await create_employee(
            session,
            business_id=business_id,
            name=admin_name,
            specialization="Администратор",
            telegram_id=telegram_id,
            role="administrator"
        )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="👥 К сотрудникам", callback_data=f"manage_employees:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await message.answer(
        f"✅ <b>Администратор назначен!</b>\n\n"
        f"👔 Имя: {admin_name}\n"
        f"🆔 Telegram ID: {telegram_id}\n"
        f"🏢 Бизнес: {business.name}\n\n"
        f"Теперь этот пользователь может:\n"
        f"• Записывать клиентов к любому мастеру\n"
        f"• Отменять записи\n"
        f"• Видеть статистику и клиентов{warning_text}",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.clear()


@router.callback_query(F.data.startswith("cancel_add_admin:"))
async def cancel_add_administrator(callback: CallbackQuery, state: FSMContext):
    """Отмена назначения администратора с удалением сообщения"""
    business_id = int(callback.data.split(":")[1])

    # Удаляем текущее сообщение (с кнопкой отмены)
    try:
        await callback.message.delete()
    except Exception:
        pass

    await state.clear()

    # Возвращаемся к управлению сотрудниками
    async with async_session_maker() as session:
        employees = await get_business_employees(session, business_id)
        business = await get_business_by_id(session, business_id)

    if not employees:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="➕ Добавить сотрудника", callback_data=f"add_employee:{business_id}")
        builder.button(text="👔 Назначить администратора", callback_data=f"add_administrator:{business_id}")
        builder.button(text="◀️ Назад", callback_data=f"business:{business_id}")
        builder.adjust(1)

        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=f"👥 <b>Сотрудники: {business.name}</b>\n\nСотрудников пока нет.",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.bot.send_message(
            chat_id=callback.from_user.id,
            text=f"👥 <b>Сотрудники: {business.name}</b>\n\nВыберите сотрудника:",
            parse_mode="HTML",
            reply_markup=admin_kb.get_employee_list_keyboard(employees, business_id)
        )

    await callback.answer()


# ===== НАСТРОЙКИ ПРЕДОПЛАТЫ =====

@router.callback_query(F.data.startswith("edit_prepayment:"))
async def show_prepayment_settings(callback: CallbackQuery, state: FSMContext):
    """Показать настройки предоплаты"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)

    if not business:
        await callback.answer("Бизнес не найден", show_alert=True)
        return

    # Формируем текст с текущими настройками
    require_receipt = getattr(business, 'require_receipt_for_prepayment', False)
    has_cancellation_photo = bool(getattr(business, 'cancellation_policy_photo', None))
    has_cancellation_text = bool(getattr(business, 'cancellation_policy_text', None))

    if business.prepayment_enabled:
        settings_text = (
            f"💳 <b>Настройки предоплаты: {business.name}</b>\n\n"
            f"✅ Предоплата: <b>включена</b>\n"
            f"💰 Процент: <b>{business.prepayment_percent or 100}%</b>\n"
            f"📱 Номер для оплаты: <b>{business.payment_phone or 'не указан'}</b>\n"
        )
        if business.payment_details:
            settings_text += f"📝 Реквизиты: {business.payment_details}\n"
        settings_text += f"📸 Чек: <b>{'обязателен' if require_receipt else 'не обязателен'}</b>\n"
        settings_text += f"🖼 Фото условий отмены: <b>{'загружено' if has_cancellation_photo else 'не загружено'}</b>\n"
        settings_text += f"📝 Текст условий отмены: <b>{'задан' if has_cancellation_text else 'не задан'}</b>\n"
        if has_cancellation_text:
            text_preview = getattr(business, 'cancellation_policy_text', '')
            if len(text_preview) > 100:
                text_preview = text_preview[:100] + "..."
            settings_text += f"\n<i>{text_preview}</i>\n"
    else:
        settings_text = (
            f"💳 <b>Настройки предоплаты: {business.name}</b>\n\n"
            f"❌ Предоплата: <b>отключена</b>\n\n"
            f"Включите предоплату, чтобы клиенты вносили оплату перед бронированием."
        )

    await callback.message.edit_text(
        settings_text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_prepayment_settings_keyboard(
            business_id, business.prepayment_enabled,
            require_receipt=require_receipt,
            has_cancellation_photo=has_cancellation_photo,
            has_cancellation_text=has_cancellation_text,
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("prepay_toggle:"))
async def toggle_prepayment(callback: CallbackQuery, state: FSMContext):
    """Включить/выключить предоплату"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    action = parts[2]  # on или off

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

        if action == "on":
            # Включаем предоплату
            business.prepayment_enabled = True
            if not business.prepayment_percent:
                business.prepayment_percent = 100  # По умолчанию 100%
            await session.commit()

            # Если нет номера для оплаты, просим ввести
            if not business.payment_phone:
                await state.update_data(business_id=business_id)
                await callback.message.edit_text(
                    "✅ Предоплата включена!\n\n"
                    "📱 Введите номер телефона для получения оплаты\n"
                    "(например: +79001234567):"
                )
                await state.set_state(PrepaymentSettingsStates.entering_phone)
                await callback.answer()
                return
        else:
            # Выключаем предоплату
            business.prepayment_enabled = False
            await session.commit()

    # Показываем обновленные настройки
    await show_prepayment_settings(callback, state)


@router.callback_query(F.data.startswith("prepay_percent:"))
async def start_edit_prepayment_percent(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование процента предоплаты"""
    business_id = int(callback.data.split(":")[1])

    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_percent = business.prepayment_percent or 100

    await callback.message.edit_text(
        f"💰 <b>Изменение процента предоплаты</b>\n\n"
        f"Текущий процент: <code>{current_percent}</code>\n\n"
        "Введите процент предоплаты (1-100):\n\n"
        "Например:\n"
        "• 100 - полная предоплата\n"
        "• 50 - половина суммы\n"
        "• 30 - 30% от стоимости",
        parse_mode="HTML"
    )
    await state.set_state(PrepaymentSettingsStates.entering_percent)
    await callback.answer()


@router.message(PrepaymentSettingsStates.entering_percent)
async def save_prepayment_percent(message: Message, state: FSMContext):
    """Сохранение процента предоплаты"""
    try:
        percent = int(message.text)
        if percent < 1 or percent > 100:
            raise ValueError()
    except ValueError:
        await message.answer(
            "❌ Введите число от 1 до 100!\n\n"
            "Попробуйте снова:"
        )
        return

    data = await state.get_data()
    business_id = data['business_id']

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.prepayment_percent = percent
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Процент предоплаты изменён на {percent}%",
        reply_markup=admin_kb.get_prepayment_settings_keyboard(business_id, True)
    )


@router.callback_query(F.data.startswith("prepay_phone:"))
async def start_edit_payment_phone(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование номера для оплаты"""
    business_id = int(callback.data.split(":")[1])

    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_phone = business.payment_phone

    if current_phone:
        text = (
            f"📱 <b>Изменение номера для оплаты</b>\n\n"
            f"Текущий номер: <code>{current_phone}</code>\n\n"
            "Введите новый номер телефона для получения предоплаты\n"
            "(например: +79001234567):\n\n"
            "На этот номер клиенты будут переводить предоплату."
        )
    else:
        text = (
            "📱 <b>Изменение номера для оплаты</b>\n\n"
            "Введите номер телефона для получения предоплаты\n"
            "(например: +79001234567):\n\n"
            "На этот номер клиенты будут переводить предоплату."
        )

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(PrepaymentSettingsStates.entering_phone)
    await callback.answer()


@router.message(PrepaymentSettingsStates.entering_phone)
async def save_payment_phone(message: Message, state: FSMContext):
    """Сохранение номера для оплаты"""
    from bot.utils.validators import validate_phone

    if not validate_phone(message.text):
        await message.answer(
            "❌ Неверный формат телефона!\n"
            "Используйте формат: +7XXXXXXXXXX или 8XXXXXXXXXX\n\n"
            "Попробуйте снова:"
        )
        return

    phone = format_phone(message.text)

    data = await state.get_data()
    business_id = data['business_id']

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.payment_phone = phone
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Номер для оплаты установлен: {phone}",
        reply_markup=admin_kb.get_prepayment_settings_keyboard(business_id, True)
    )


@router.callback_query(F.data.startswith("prepay_details:"))
async def start_edit_payment_details(callback: CallbackQuery, state: FSMContext):
    """Начать редактирование реквизитов"""
    business_id = int(callback.data.split(":")[1])

    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_details = business.payment_details

    if current_details:
        text = (
            f"📝 <b>Изменение реквизитов для оплаты</b>\n\n"
            f"Текущие реквизиты:\n<code>{current_details}</code>\n\n"
            "Введите новые реквизиты:\n"
            "(номер карты, СБП, название банка и т.д.)\n\n"
            "Или '-' чтобы удалить реквизиты."
        )
    else:
        text = (
            "📝 <b>Изменение реквизитов для оплаты</b>\n\n"
            "Реквизиты не заданы.\n\n"
            "Введите дополнительные реквизиты:\n"
            "(номер карты, СБП, название банка и т.д.)\n\n"
            "Или '-' чтобы удалить реквизиты."
        )

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(PrepaymentSettingsStates.entering_details)
    await callback.answer()


@router.message(PrepaymentSettingsStates.entering_details)
async def save_payment_details(message: Message, state: FSMContext):
    """Сохранение реквизитов"""
    data = await state.get_data()
    business_id = data['business_id']

    details = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.payment_details = details
        await session.commit()

    await state.clear()

    if details:
        await message.answer(
            f"✅ Реквизиты сохранены",
            reply_markup=admin_kb.get_prepayment_settings_keyboard(business_id, True)
        )
    else:
        await message.answer(
            f"✅ Реквизиты удалены",
            reply_markup=admin_kb.get_prepayment_settings_keyboard(business_id, True)
        )


@router.callback_query(F.data.startswith("prepay_require_receipt:"))
async def toggle_require_receipt(callback: CallbackQuery, state: FSMContext):
    """Включить/выключить обязательность чека для подтверждения предоплаты"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    action = parts[2]  # on или off

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return
        business.require_receipt_for_prepayment = (action == "on")
        await session.commit()

    # Показываем обновленные настройки
    await show_prepayment_settings(callback, state)


@router.callback_query(F.data.startswith("prepay_cancel_photo:"))
async def manage_cancellation_photo(callback: CallbackQuery, state: FSMContext):
    """Управление фото условий отмены бронирования"""
    business_id = int(callback.data.split(":")[1])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_photo = getattr(business, 'cancellation_policy_photo', None)

    if current_photo:
        text = (
            "🖼 <b>Фото условий отмены бронирования</b>\n\n"
            "Текущее фото уже загружено.\n\n"
            "Отправьте новое фото для замены, или '-' чтобы удалить текущее."
        )
    else:
        text = (
            "📷 <b>Фото условий отмены бронирования</b>\n\n"
            "Это фото будет отправлено клиентам вместе с информацией о предоплате.\n\n"
            "Отправьте фото с условиями отмены бронирования."
        )

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(PrepaymentSettingsStates.entering_cancellation_photo)
    await callback.answer()


@router.message(PrepaymentSettingsStates.entering_cancellation_photo)
async def save_cancellation_photo(message: Message, state: FSMContext):
    """Сохранение фото условий отмены бронирования"""
    data = await state.get_data()
    business_id = data['business_id']

    if message.text == "-":
        photo_id = None
    elif message.photo:
        photo_id = message.photo[-1].file_id
    else:
        await message.answer("❌ Отправьте фото или '-' для удаления текущего фото.")
        return

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.cancellation_policy_photo = photo_id
        require_receipt = getattr(business, 'require_receipt_for_prepayment', False)
        await session.commit()

    await state.clear()
    has_cancellation_text = bool(getattr(business, 'cancellation_policy_text', None))
    await message.answer(
        "✅ Фото условий отмены сохранено." if photo_id else "✅ Фото условий отмены удалено.",
        reply_markup=admin_kb.get_prepayment_settings_keyboard(
            business_id, True,
            require_receipt=require_receipt,
            has_cancellation_photo=bool(photo_id),
            has_cancellation_text=has_cancellation_text,
        )
    )


@router.callback_query(F.data.startswith("prepay_cancel_text:"))
async def manage_cancellation_text(callback: CallbackQuery, state: FSMContext):
    """Управление текстом условий отмены бронирования"""
    business_id = int(callback.data.split(":")[1])
    await state.update_data(business_id=business_id)

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        current_text = getattr(business, 'cancellation_policy_text', None)

    if current_text:
        text = (
            "📝 <b>Текст условий отмены бронирования</b>\n\n"
            f"Текущий текст:\n<i>{current_text}</i>\n\n"
            "Введите новый текст для замены, или '-' чтобы удалить текущий.\n\n"
            "Этот текст будет показан клиентам в красной рамке при бронировании."
        )
    else:
        text = (
            "✏️ <b>Текст условий отмены бронирования</b>\n\n"
            "Этот текст будет показан клиентам в красной рамке при бронировании через веб-версию.\n\n"
            "Введите текст условий отмены бронирования:"
        )

    await callback.message.edit_text(text, parse_mode="HTML")
    await state.set_state(PrepaymentSettingsStates.entering_cancellation_text)
    await callback.answer()


@router.message(PrepaymentSettingsStates.entering_cancellation_text)
async def save_cancellation_text(message: Message, state: FSMContext):
    """Сохранение текста условий отмены бронирования"""
    data = await state.get_data()
    business_id = data['business_id']

    cancel_text = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        business.cancellation_policy_text = cancel_text
        require_receipt = getattr(business, 'require_receipt_for_prepayment', False)
        has_cancellation_photo = bool(getattr(business, 'cancellation_policy_photo', None))
        await session.commit()

    await state.clear()
    await message.answer(
        "✅ Текст условий отмены сохранён." if cancel_text else "✅ Текст условий отмены удалён.",
        reply_markup=admin_kb.get_prepayment_settings_keyboard(
            business_id, True,
            require_receipt=require_receipt,
            has_cancellation_photo=has_cancellation_photo,
            has_cancellation_text=bool(cancel_text),
        )
    )


# ===== ПОДТВЕРЖДЕНИЕ ПРЕДОПЛАТЫ ВЛАДЕЛЬЦЕМ =====

@router.callback_query(F.data.startswith("confirm_prepayment:"))
async def confirm_prepayment_by_owner(callback: CallbackQuery, state: FSMContext):
    """Owner confirms prepayment; the whole visit becomes a confirmed booking."""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        from sqlalchemy.orm import selectinload
        from database.booking_safety import contiguous_visit_bookings
        from database.crud import get_business_owners

        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.client),
                selectinload(Booking.service),
                selectinload(Booking.business),
                selectinload(Booking.employee),
            )
            .where(Booking.id == booking_id)
        )
        booking = result.scalar_one_or_none()
        if not booking:
            await callback.answer("Бронирование не найдено", show_alert=True)
            return

        owners = await get_business_owners(session, booking.business_id)
        owner_telegram_ids = {
            int(owner.telegram_id)
            for owner in owners
            if owner.telegram_id and int(owner.telegram_id) > 0
        }
        if callback.from_user.id not in owner_telegram_ids:
            await callback.answer("У вас нет прав подтверждать эту оплату", show_alert=True)
            return

        if booking.status not in ("prepayment_sent", "pending_prepayment"):
            await callback.answer("Статус бронирования уже изменён", show_alert=True)
            return

        from database.prepayment import review_payment, PaymentWindowError
        reviewer = next(owner for owner in owners if owner.telegram_id == callback.from_user.id)
        try:
            expected = int(callback.data.split(":")[2]) if len(callback.data.split(":")) > 2 else None
            related_bookings = await review_payment(session, booking, reviewer.id, "confirm", expected_version=expected)
        except PaymentWindowError as exc:
            await callback.answer(exc.detail, show_alert=True)
            return
        await session.commit()

        try:
            from bot.services.email_notifications import send_booking_email
            await send_booking_email([booking], "prepayment_confirmed")
        except Exception as email_error:
            logger.warning(
                "Email prepayment confirmation failed for booking %s: %s",
                booking.id,
                email_error,
            )

        # Optional visual context remains a Telegram enhancement; the actual
        # booking-created notification below is transport-neutral.
        if (
            booking.client
            and booking.client.telegram_id
            and int(booking.client.telegram_id) > 0
            and booking.business
            and booking.business.photos
        ):
            try:
                import json
                from aiogram.types import InputMediaPhoto

                photos_data = json.loads(booking.business.photos)
                if isinstance(photos_data, list) and photos_data:
                    media_group = []
                    for index, photo_id in enumerate(photos_data[:5]):
                        if index == 0:
                            media_group.append(
                                InputMediaPhoto(
                                    media=photo_id,
                                    caption=f"📷 {booking.business.name}",
                                )
                            )
                        else:
                            media_group.append(InputMediaPhoto(media=photo_id))
                    await callback.bot.send_media_group(
                        chat_id=booking.client.telegram_id,
                        media=media_group,
                    )
            except Exception as photo_error:
                logger.warning(
                    "Business photos after prepayment confirmation failed for booking %s: %s",
                    booking.id,
                    photo_error,
                )

        try:
            from bot.services.notification_events import dispatch_booking_event

            await dispatch_booking_event(
                related_bookings,
                session,
                "booking_created",
                include_client=True,
                staff_audience="all",
            )
        except Exception as notification_error:
            logger.error(
                "Confirmed-booking notification failed for booking %s: %s",
                booking.id,
                notification_error,
                exc_info=True,
            )

    await callback.message.edit_text(
        callback.message.text
        + "\n\n✅ <b>Предоплата подтверждена!</b>\nЗапись подтверждена, уведомления отправлены.",
        parse_mode="HTML",
    )
    await callback.answer("Предоплата подтверждена!")


@router.callback_query(F.data.startswith("reject_prepayment:"))
async def reject_prepayment_by_owner(callback: CallbackQuery, state: FSMContext):
    """Owner rejects an unreceived prepayment for the whole visit."""
    booking_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        from sqlalchemy.orm import selectinload
        from database.booking_safety import contiguous_visit_bookings
        from database.crud import get_business_owners

        booking = (
            await session.execute(
                select(Booking)
                .options(
                    selectinload(Booking.client),
                    selectinload(Booking.service),
                    selectinload(Booking.business),
                    selectinload(Booking.employee),
                )
                .where(Booking.id == booking_id)
            )
        ).scalar_one_or_none()

        if not booking:
            await callback.answer("Бронирование не найдено", show_alert=True)
            return

        owners = await get_business_owners(session, booking.business_id)
        owner_telegram_ids = {
            int(owner.telegram_id)
            for owner in owners
            if owner.telegram_id and int(owner.telegram_id) > 0
        }
        if callback.from_user.id not in owner_telegram_ids:
            await callback.answer("У вас нет прав отклонять эту оплату", show_alert=True)
            return

        if booking.status not in ("prepayment_sent", "pending_prepayment"):
            await callback.answer("Статус бронирования уже изменён", show_alert=True)
            return

        from database.prepayment import review_payment, PaymentWindowError
        reviewer = next(owner for owner in owners if owner.telegram_id == callback.from_user.id)
        try:
            expected = int(callback.data.split(":")[2]) if len(callback.data.split(":")) > 2 else None
            related_bookings = await review_payment(session, booking, reviewer.id, "reject", expected_version=expected)
        except PaymentWindowError as exc:
            await callback.answer(exc.detail, show_alert=True)
            return
        await session.commit()

        try:
            from bot.services.email_notifications import send_booking_email
            await send_booking_email([booking], "prepayment_rejected")
        except Exception as email_error:
            logger.warning(
                "Email prepayment rejection failed for booking %s: %s",
                booking.id,
                email_error,
            )

        try:
            from bot.services.webpush import send_booking_status_webpush
            await send_booking_status_webpush(
                related_bookings,
                session,
                "prepayment_rejected",
            )
            await session.commit()
        except Exception as push_error:
            logger.warning(
                "Web Push rejection failed for booking %s: %s",
                booking.id,
                push_error,
            )

        if booking.client and booking.client.telegram_id and booking.client.telegram_id > 0:
            from bot.keyboards.client_kb import get_prepayment_confirmation_keyboard

            client_text = (
                f"❌ <b>Предоплата не подтверждена</b>\n\n"
                f"Владелец {booking.business.name} не получил вашу предоплату.\n\n"
                "У вас ещё 10 минут, чтобы проверить перевод и отправить верный чек. "
                "Если деньги уже списаны, не переводите их повторно — свяжитесь с бизнесом."
            )
            try:
                await callback.bot.send_message(
                    chat_id=booking.client.telegram_id,
                    text=client_text,
                    parse_mode="HTML",
                    reply_markup=get_prepayment_confirmation_keyboard(
                        booking.id,
                        require_receipt=bool(
                            getattr(
                                booking.business,
                                "require_receipt_for_prepayment",
                                False,
                            )
                        ),
                    ),
                )
            except Exception as telegram_error:
                logger.warning(
                    "Telegram rejection notice failed booking=%s: %s",
                    booking.id,
                    telegram_error,
                )

    await callback.message.edit_text(
        callback.message.text
        + "\n\n❌ <b>Предоплата не подтверждена</b>\nКлиент уведомлён.",
        parse_mode="HTML",
    )
    await callback.answer("Предоплата отклонена")


# ===== УПРАВЛЕНИЕ СОВЛАДЕЛЬЦАМИ =====

@router.callback_query(F.data.startswith("manage_owners:"))
async def manage_business_owners(callback: CallbackQuery, state: FSMContext):
    """Показать список совладельцев бизнеса"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        # Проверяем права доступа
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

        owners_data = await get_business_owners_with_roles(session, business_id)

    text = f"👥 <b>Совладельцы бизнеса «{business.name}»</b>\n\n"

    if owners_data:
        for i, owner_info in enumerate(owners_data, 1):
            user_obj = owner_info['user']
            role = owner_info['role']
            role_text = "👑 Главный владелец" if role == "owner" else "👤 Совладелец"

            name = user_obj.full_name or "Без имени"
            username = f"@{user_obj.username}" if user_obj.username else ""
            text += f"{i}. {name} {username}\n   {role_text}\n\n"
    else:
        text += "Владельцы не найдены.\n"

    text += "\nВыберите владельца для просмотра информации или добавьте нового:"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_business_owners_keyboard(business_id, owners_data)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("owner_info:"))
async def show_owner_info(callback: CallbackQuery, state: FSMContext):
    """Показать информацию о конкретном совладельце"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    owner_user_id = int(parts[2])

    async with async_session_maker() as session:
        # Проверяем права доступа
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        owners_data = await get_business_owners_with_roles(session, business_id)
        owners_count = await get_owners_count(session, business_id)

    # Находим нужного владельца
    owner_info = None
    for o in owners_data:
        if o['user'].id == owner_user_id:
            owner_info = o
            break

    if not owner_info:
        await callback.answer("Владелец не найден", show_alert=True)
        return

    owner_user = owner_info['user']
    role = owner_info['role']
    added_at = owner_info['added_at']

    role_text = "👑 Главный владелец" if role == "owner" else "👤 Совладелец"
    name = owner_user.full_name or "Без имени"
    username = f"@{owner_user.username}" if owner_user.username else "нет username"

    text = f"""👤 <b>Информация о совладельце</b>

🏢 Бизнес: {business.name}

📛 Имя: {name}
📱 Username: {username}
🆔 Telegram ID: <code>{owner_user.telegram_id}</code>
👔 Роль: {role_text}
📅 Добавлен: {added_at.strftime('%d.%m.%Y') if added_at else 'неизвестно'}
"""

    # Нельзя удалить последнего владельца или главного владельца (если он единственный)
    can_remove = owners_count > 1 and not (role == "owner" and owners_count == 1)

    # Нельзя удалить себя, если ты главный владелец
    if owner_user.id == user.id and role == "owner":
        can_remove = False

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=admin_kb.get_owner_info_keyboard(business_id, owner_user_id, can_remove)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("add_owner:"))
async def start_add_owner(callback: CallbackQuery, state: FSMContext):
    """Начать добавление совладельца"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

    await state.update_data(business_id=business_id)
    await state.set_state(BusinessOwnerStates.entering_telegram_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data=f"manage_owners:{business_id}")

    await callback.message.edit_text(
        f"➕ <b>Добавление совладельца</b>\n\n"
        f"Бизнес: {business.name}\n\n"
        f"Введите Telegram ID или @username нового совладельца:\n\n"
        f"<i>Совладелец получит полный доступ к управлению бизнесом.</i>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.message(BusinessOwnerStates.entering_telegram_id)
async def process_new_owner_id(message: Message, state: FSMContext):
    """Обработка ввода Telegram ID нового совладельца"""
    data = await state.get_data()
    business_id = data.get('business_id')

    if not business_id:
        await message.answer("Ошибка: бизнес не выбран. Начните заново.")
        await state.clear()
        return

    input_text = message.text.strip()

    # Определяем, это ID или username
    telegram_id = None
    if input_text.startswith('@'):
        # Это username - ищем пользователя
        username = input_text[1:]  # Убираем @
        async with async_session_maker() as session:
            from sqlalchemy import select
            from database.models import User
            result = await session.execute(
                select(User).where(User.username == username)
            )
            found_user = result.scalar_one_or_none()
            if found_user:
                telegram_id = found_user.telegram_id
            else:
                await message.answer(
                    f"❌ Пользователь с username @{username} не найден в базе.\n\n"
                    f"Пользователь должен сначала запустить бота командой /start.\n"
                    f"Попробуйте ввести Telegram ID напрямую или другой username."
                )
                return
    else:
        # Пытаемся распарсить как число
        try:
            telegram_id = int(input_text)
        except ValueError:
            await message.answer(
                "❌ Неверный формат.\n\n"
                "Введите числовой Telegram ID или @username."
            )
            return

    async with async_session_maker() as session:
        # Проверяем права текущего пользователя
        current_user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        if not await is_business_owner(session, business_id, current_user.id):
            await message.answer("У вас нет доступа к этому бизнесу")
            await state.clear()
            return

        # Находим пользователя по telegram_id
        new_owner = await get_user_by_telegram_id(session, telegram_id)

        if not new_owner:
            await message.answer(
                f"❌ Пользователь с Telegram ID {telegram_id} не найден в базе.\n\n"
                f"Пользователь должен сначала запустить бота командой /start."
            )
            return

        # Проверяем, не является ли уже владельцем
        if await is_business_owner(session, business_id, new_owner.id):
            await message.answer("❌ Этот пользователь уже является совладельцем данного бизнеса.")
            return

        # Добавляем совладельца
        result = await add_business_owner(
            session,
            business_id=business_id,
            user_id=new_owner.id,
            role="co_owner",
            added_by_id=current_user.id
        )

        if result:
            business = await get_business_by_id(session, business_id)
            owners_data = await get_business_owners_with_roles(session, business_id)

            new_owner_name = new_owner.full_name or new_owner.username or f"ID: {new_owner.telegram_id}"

            await message.answer(
                f"✅ <b>Совладелец добавлен!</b>\n\n"
                f"👤 {new_owner_name}\n"
                f"🏢 Бизнес: {business.name}\n\n"
                f"Теперь этот пользователь может управлять бизнесом.",
                parse_mode="HTML",
                reply_markup=admin_kb.get_business_owners_keyboard(business_id, owners_data)
            )

            # Уведомляем нового совладельца с web-credentials
            try:
                import hmac
                from bot.config import BOT_TOKEN, WEBAPP_URL
                
                # Генерируем web credentials
                token = hmac.new(
                    BOT_TOKEN.encode(),
                    f"web_owner_{new_owner.telegram_id}".encode(),
                    hashlib.sha256
                ).hexdigest()
                pin_num = int(token[:8], 16) % 1000000
                pin = f"{pin_num:06d}"
                login_url = f"{WEBAPP_URL}/auth/token/{token}"
                
                notification_text = f"🎉 <b>Вы стали совладельцем бизнеса!</b>\n\n🏢 {business.name}\n\nТеперь вы можете управлять этим бизнесом в разделе «🏢 Мои бизнесы».\n\n<b>🔐 Веб-доступ:</b>\n📱 <b>Телефон:</b> {new_owner.phone or '(не указан)'}\n🔑 <b>PIN-код:</b> <code>{pin}</code>\n\n<b>Или используйте прямую ссылку:</b>\n{login_url}\n\n💡 Сохраните данные в закладки!"
                
                await message.bot.send_message(
                    chat_id=new_owner.telegram_id,
                    text=notification_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True
                )
            except Exception as e:
                print(f"Не удалось уведомить нового совладельца: {e}")
        else:
            await message.answer("❌ Не удалось добавить совладельца. Возможно, он уже добавлен.")

    await state.clear()


@router.callback_query(F.data.startswith("remove_owner:"))
async def confirm_remove_owner(callback: CallbackQuery, state: FSMContext):
    """Запрос подтверждения удаления совладельца"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    owner_user_id = int(parts[2])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        owner_to_remove = await get_user_by_telegram_id(session, owner_user_id)

        if not owner_to_remove:
            # Пробуем найти по user.id
            from sqlalchemy import select
            from database.models import User
            result = await session.execute(select(User).where(User.id == owner_user_id))
            owner_to_remove = result.scalar_one_or_none()

    if not owner_to_remove:
        await callback.answer("Пользователь не найден", show_alert=True)
        return

    owner_name = owner_to_remove.full_name or owner_to_remove.username or f"ID: {owner_to_remove.telegram_id}"

    await callback.message.edit_text(
        f"⚠️ <b>Подтверждение удаления</b>\n\n"
        f"Вы уверены, что хотите удалить совладельца?\n\n"
        f"👤 {owner_name}\n"
        f"🏢 Бизнес: {business.name}\n\n"
        f"<i>Пользователь потеряет доступ к управлению бизнесом.</i>",
        parse_mode="HTML",
        reply_markup=admin_kb.get_confirm_remove_owner_keyboard(business_id, owner_to_remove.id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_remove_owner:"))
async def execute_remove_owner(callback: CallbackQuery, state: FSMContext):
    """Выполнить удаление совладельца"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    owner_user_id = int(parts[2])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)

        # Получаем информацию о удаляемом владельце до удаления
        owner_to_remove = None
        from sqlalchemy import select
        from database.models import User
        result = await session.execute(select(User).where(User.id == owner_user_id))
        owner_to_remove = result.scalar_one_or_none()

        # Удаляем совладельца
        success, message_text = await remove_business_owner(session, business_id, owner_user_id)

        if success:
            owners_data = await get_business_owners_with_roles(session, business_id)

            await callback.message.edit_text(
                f"✅ <b>Совладелец удалён</b>\n\n"
                f"🏢 Бизнес: {business.name}",
                parse_mode="HTML",
                reply_markup=admin_kb.get_business_owners_keyboard(business_id, owners_data)
            )

            # Уведомляем удалённого совладельца
            if owner_to_remove:
                try:
                    await callback.bot.send_message(
                        chat_id=owner_to_remove.telegram_id,
                        text=f"ℹ️ <b>Изменение доступа</b>\n\n"
                             f"Вы были удалены из совладельцев бизнеса «{business.name}».",
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Не удалось уведомить удалённого совладельца: {e}")
        else:
            await callback.answer(message_text, show_alert=True)
            return

    await callback.answer("Совладелец удалён")


# ===== QR-КОД ДЛЯ КЛИЕНТОВ =====

@router.callback_query(F.data.startswith("show_qr:"))
async def show_business_qr_code(callback: CallbackQuery, state: FSMContext):
    """Показать QR-код для записи клиентов"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

    # Генерируем QR-код
    import qrcode
    from io import BytesIO
    from aiogram.types import BufferedInputFile
    from bot.config import BOT_USERNAME

    deep_link = f"https://t.me/{BOT_USERNAME}?start=book_{business_id}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(deep_link)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Сохраняем в BytesIO
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    # Отправляем QR-код
    photo = BufferedInputFile(buffer.read(), filename="qr_code.png")

    await callback.message.answer_photo(
        photo=photo,
        caption=f"📲 <b>QR-код для записи в «{business.name}»</b>\n\n"
                f"Клиенты могут отсканировать этот код, чтобы сразу перейти к записи.\n\n"
                f"🔗 Ссылка: {deep_link}",
        parse_mode="HTML"
    )

    await callback.answer()




# ===== QR-КОД ДЛЯ WEBAPP =====

@router.callback_query(F.data.startswith("show_webapp_qr:"))
async def show_business_webapp_qr_code(callback: CallbackQuery, state: FSMContext):
    """Показать QR-код для записи через WebApp"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("У вас нет доступа к этому бизнесу", show_alert=True)
            return

        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

    from bot.config import WEBAPP_URL
    if not WEBAPP_URL:
        await callback.answer("WebApp URL не настроен", show_alert=True)
        return

    import qrcode
    from io import BytesIO
    from aiogram.types import BufferedInputFile

    webapp_link = f"{WEBAPP_URL}/book?business_id={business_id}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(webapp_link)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    photo = BufferedInputFile(buffer.read(), filename="qr_webapp.png")

    caption = (
        "🌐 <b>QR-код для онлайн-записи «" + business.name + "»</b>\n\n"
        "Клиенты сканируют код и сразу попадают на страницу записи.\n\n"
        "🔗 Ссылка: " + webapp_link
    )

    await callback.message.answer_photo(
        photo=photo,
        caption=caption,
        parse_mode="HTML"
    )
    await callback.answer()


# ===== VENUE RENTAL: ПАРАМЕТРЫ ЗАЛА (ВМЕСТИМОСТЬ, ПЛОЩАДЬ, ОПИСАНИЕ, МИН. ВРЕМЯ) =====

@router.callback_query(F.data.startswith("edit_emp_capacity:"))
async def edit_emp_capacity_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования вместимости зала"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.editing_capacity)
    await callback.message.edit_text(
        "👥 Введите <b>вместимость</b> зала (кол-во человек):\n\n"
        "Например: <code>15</code>\n"
        "Отправьте <code>0</code> чтобы убрать.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EmployeeEditStates.editing_capacity)
async def edit_emp_capacity_done(message: Message, state: FSMContext):
    """Сохранение вместимости зала"""
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число >= 0")
        return

    data = await state.get_data()
    employee_id = data['editing_employee_id']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.capacity = value if value > 0 else None
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Вместимость {'установлена: ' + str(value) + ' чел.' if value > 0 else 'убрана'}",
        parse_mode="HTML"
    )
    # Возвращаемся в меню сотрудника
    from aiogram.types import CallbackQuery as CQ
    fake_cb = type('obj', (object,), {'data': f'edit_employee:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_employee_menu(fake_cb)


@router.callback_query(F.data.startswith("edit_emp_area:"))
async def edit_emp_area_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования площади зала"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.editing_area)
    await callback.message.edit_text(
        "📐 Введите <b>площадь</b> зала (м²):\n\n"
        "Например: <code>50</code>\n"
        "Отправьте <code>0</code> чтобы убрать.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EmployeeEditStates.editing_area)
async def edit_emp_area_done(message: Message, state: FSMContext):
    """Сохранение площади зала"""
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число >= 0")
        return

    data = await state.get_data()
    employee_id = data['editing_employee_id']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.area_sqm = value if value > 0 else None
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Площадь {'установлена: ' + str(value) + ' м²' if value > 0 else 'убрана'}",
        parse_mode="HTML"
    )
    fake_cb = type('obj', (object,), {'data': f'edit_employee:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_employee_menu(fake_cb)


@router.callback_query(F.data.startswith("edit_emp_description:"))
async def edit_emp_description_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования описания зала"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.editing_description)
    await callback.message.edit_text(
        "📝 Введите <b>описание</b> зала:\n\n"
        "Например: <i>Репетиционная комната с барабанной установкой, 2 комбика, микрофон</i>\n\n"
        "Отправьте <code>-</code> чтобы убрать описание.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EmployeeEditStates.editing_description)
async def edit_emp_description_done(message: Message, state: FSMContext):
    """Сохранение описания зала"""
    data = await state.get_data()
    employee_id = data['editing_employee_id']
    text = message.text.strip()

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.description = text if text != "-" else None
        await session.commit()

    await state.clear()
    await message.answer(
        f"✅ Описание {'обновлено' if text != '-' else 'убрано'}",
        parse_mode="HTML"
    )
    fake_cb = type('obj', (object,), {'data': f'edit_employee:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_employee_menu(fake_cb)


@router.callback_query(F.data.startswith("edit_emp_min_booking:"))
async def edit_emp_min_booking_start(callback: CallbackQuery, state: FSMContext):
    """Начало редактирования мин. времени бронирования"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.editing_min_booking)
    await callback.message.edit_text(
        "⏱ Введите <b>минимальное время бронирования</b> (в минутах):\n\n"
        "Например: <code>120</code> (2 часа)\n"
        "Отправьте <code>0</code> чтобы убрать ограничение.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EmployeeEditStates.editing_min_booking)
async def edit_emp_min_booking_done(message: Message, state: FSMContext):
    """Сохранение мин. времени бронирования"""
    try:
        value = int(message.text.strip())
        if value < 0:
            raise ValueError
    except ValueError:
        await message.answer("❌ Введите целое число >= 0")
        return

    data = await state.get_data()
    employee_id = data['editing_employee_id']

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.min_booking_minutes = value if value > 0 else None
        await session.commit()

    await state.clear()
    if value > 0:
        hours = value // 60
        mins = value % 60
        time_str = f"{hours}ч {mins}мин" if hours and mins else (f"{hours}ч" if hours else f"{mins}мин")
        await message.answer(f"✅ Мин. бронирование: {time_str}")
    else:
        await message.answer("✅ Ограничение мин. времени убрано")
    fake_cb = type('obj', (object,), {'data': f'edit_employee:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_employee_menu(fake_cb)


# ===== ФОТО И ВИДЕО ЗАЛОВ =====

@router.callback_query(F.data.startswith("edit_emp_photos:"))
async def edit_emp_photos_menu(callback: CallbackQuery, state: FSMContext):
    """Меню управления фото зала"""
    employee_id = int(callback.data.split(":")[1])
    import json as _pjson
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        user = await get_user_by_telegram_id(session, callback.from_user.id)
        if not employee or not user or not await is_business_owner(session, employee.business_id, user.id):
            await callback.answer("Нет доступа к этому помещению", show_alert=True)
            return

    photos = []
    if employee.photos:
        try:
            photos = _pjson.loads(employee.photos)
        except Exception:
            pass

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить фото", callback_data=f"emp_add_photo:{employee_id}")
    if photos:
        builder.button(text="👁 Посмотреть все фото", callback_data=f"emp_view_photos:{employee_id}")
        builder.button(text="🗑 Удалить все фото", callback_data=f"emp_clear_photos:{employee_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"emp_photos_back:{employee_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"📷 <b>Фото зала: {employee.name}</b>\n\n"
        f"Загружено фото: {len(photos)}\n\n"
        "Отправьте фото прямо в чат или нажмите «➕ Добавить фото».",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    _advance_room_photo_capture(callback.message.chat.id, callback.from_user.id, employee_id, True)
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.adding_photo)
    await callback.answer()


@router.callback_query(F.data.startswith("emp_add_photo:"))
async def emp_add_photo_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления фото"""
    employee_id = int(callback.data.split(":")[1])
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        user = await get_user_by_telegram_id(session, callback.from_user.id)
        if not employee or not user or not await is_business_owner(session, employee.business_id, user.id):
            await callback.answer("Нет доступа к этому помещению", show_alert=True)
            return
    await callback.message.edit_text(
        "📷 Отправьте <b>фото</b> зала.\n\n"
        "Можно отправить несколько фото сразу.\n"
        "Когда закончите, нажмите кнопку ниже.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"emp_photos_done:{employee_id}", style="success")]
        ])
    )
    _advance_room_photo_capture(callback.message.chat.id, callback.from_user.id, employee_id, True)
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.adding_photo)
    await callback.answer()


@router.message(EmployeeEditStates.adding_photo, F.photo)
async def emp_add_photo_receive(message: Message, state: FSMContext):
    """Получение фото зала"""
    data = await state.get_data()
    employee_id = data.get('editing_employee_id')
    if not employee_id:
        await state.clear()
        await message.answer("Выберите помещение заново перед загрузкой фото.")
        return
    capture_key = (message.chat.id, message.from_user.id, employee_id)
    capture_generation, capture_open = _room_photo_capture_sessions.get(capture_key, (0, True))
    import json as _pjson

    # Берём фото максимального размера
    file_id = message.photo[-1].file_id

    from database import Employee
    async with async_session_maker() as session:
        result = await session.execute(select(Employee).where(Employee.id == employee_id).with_for_update())
        employee = result.scalar_one_or_none()
        user = await get_user_by_telegram_id(session, message.from_user.id)
        if not employee or not user or not await is_business_owner(session, employee.business_id, user.id):
            await state.clear()
            await message.answer("Нет доступа к этому помещению. Выберите его заново.")
            return
        photos = []
        if employee.photos:
            try:
                photos = _pjson.loads(employee.photos)
            except Exception:
                pass
        photos.append(file_id)
        employee.photos = _pjson.dumps(photos)
        await session.commit()

    if not capture_open or _room_photo_capture_sessions.get(capture_key, (0, True)) != (capture_generation, True):
        await _save_photo_to_cache(message.bot, file_id)
        return

    # Учитываем элемент альбома сразу после commit: сетевой web-кеш может задержать
    # отдельные обработчики и не должен разбивать один альбом на два уведомления.
    group_id = getattr(message, "media_group_id", None)
    if group_id:
        key = (message.chat.id, message.from_user.id, group_id, employee_id)
        batch = _room_photo_album_pending.setdefault(key, {"count": 0})
        batch["count"] += 1
        batch["total"] = len(photos)
        batch["message"] = message
        previous = batch.get("task")
        if previous and not previous.done():
            previous.cancel()
        batch["task"] = asyncio.create_task(_send_room_photo_album_summary(key))
        await _save_photo_to_cache(message.bot, file_id)
        return

    # Кешируем одиночное фото для webapp.
    await _save_photo_to_cache(message.bot, file_id)
    if _room_photo_capture_sessions.get(capture_key, (0, True)) != (capture_generation, True):
        return
    await message.answer(
        f"✅ Фото добавлено (всего: {len(photos)})\n\nОтправьте ещё или нажмите Готово.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Готово", callback_data=f"emp_photos_done:{employee_id}", style="success")]
        ])
    )


@router.callback_query(F.data.startswith("emp_photos_done:"))
async def emp_photos_done(callback: CallbackQuery, state: FSMContext):
    """Завершение добавления фото"""
    _advance_room_photo_capture(callback.message.chat.id, callback.from_user.id, int(callback.data.split(":")[1]), False)
    _cancel_room_photo_album_summaries(callback)
    await state.clear()
    await edit_employee_menu(callback)


@router.callback_query(F.data.startswith("emp_photos_back:"))
async def emp_photos_back(callback: CallbackQuery, state: FSMContext):
    """Вернуться к помещению и перестать принимать фотографии."""
    _advance_room_photo_capture(callback.message.chat.id, callback.from_user.id, int(callback.data.split(":")[1]), False)
    _cancel_room_photo_album_summaries(callback)
    await state.clear()
    await edit_employee_menu(callback)


@router.callback_query(F.data.startswith("emp_view_photos:"))
async def emp_view_photos(callback: CallbackQuery):
    """Просмотр всех фото зала"""
    employee_id = int(callback.data.split(":")[1])
    import json as _pjson
    from database.crud import get_employee_by_id
    from aiogram.types import InputMediaPhoto
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)

    photos = []
    if employee.photos:
        try:
            photos = _pjson.loads(employee.photos)
        except Exception:
            pass

    if not photos:
        await callback.answer("Нет фото", show_alert=True)
        return

    # Отправляем медиагруппу (до 10 фото за раз)
    for i in range(0, len(photos), 10):
        batch = photos[i:i+10]
        media = [InputMediaPhoto(media=fid) for fid in batch]
        if i == 0:
            media[0].caption = f"📷 Фото: {employee.name}"
        await callback.message.answer_media_group(media)

    await callback.answer()


@router.callback_query(F.data.startswith("emp_clear_photos:"))
async def emp_clear_photos(callback: CallbackQuery, state: FSMContext):
    """Удаление всех фото зала"""
    employee_id = int(callback.data.split(":")[1])
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.photos = None
        await session.commit()

    await callback.answer("✅ Все фото удалены", show_alert=True)
    fake_cb = type('obj', (object,), {'data': f'edit_emp_photos:{employee_id}', 'message': callback.message, 'from_user': callback.from_user, 'answer': lambda *a, **kw: None})()
    await edit_emp_photos_menu(fake_cb, state)


@router.callback_query(F.data.startswith("edit_emp_video:"))
async def edit_emp_video_menu(callback: CallbackQuery, state: FSMContext):
    """Меню управления видео зала"""
    employee_id = int(callback.data.split(":")[1])
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    if employee.video:
        builder.button(text="👁 Посмотреть видео", callback_data=f"emp_view_video:{employee_id}")
        builder.button(text="🔄 Заменить видео", callback_data=f"emp_set_video:{employee_id}")
        builder.button(text="🗑 Удалить видео", callback_data=f"emp_clear_video:{employee_id}", style="danger")
    else:
        builder.button(text="➕ Добавить видео", callback_data=f"emp_set_video:{employee_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_employee:{employee_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        f"🎥 <b>Видео зала: {employee.name}</b>\n\n"
        f"Видео: {'загружено' if employee.video else 'не загружено'}",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data.startswith("emp_set_video:"))
async def emp_set_video_start(callback: CallbackQuery, state: FSMContext):
    """Начало добавления/замены видео"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(editing_employee_id=employee_id)
    await state.set_state(EmployeeEditStates.adding_video)
    await callback.message.edit_text(
        "🎥 Отправьте <b>видео</b> зала.\n\n"
        "📎 Максимальный размер: 15 МБ\n"
        "Поддерживаются видео и видеокружки.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(EmployeeEditStates.adding_video, F.video)
async def emp_add_video_receive(message: Message, state: FSMContext):
    """Получение видео зала"""
    max_size_mb = 100
    if message.video.file_size and message.video.file_size > max_size_mb * 1024 * 1024:
        await message.answer(f"❌ Видео слишком большое. Максимум {max_size_mb} МБ.")
        return

    data = await state.get_data()
    employee_id = data['editing_employee_id']

    file_id = message.video.file_id

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.video = file_id
        await session.commit()

    # Скачиваем для webapp
    await _save_video_to_cache(message.bot, file_id)

    await state.clear()
    await message.answer("✅ Видео сохранено")
    fake_cb = type('obj', (object,), {'data': f'edit_emp_video:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_emp_video_menu(fake_cb, state)


@router.message(EmployeeEditStates.adding_video, F.video_note)
async def emp_add_video_note_receive(message: Message, state: FSMContext):
    """Получение видеокружки зала"""
    data = await state.get_data()
    employee_id = data['editing_employee_id']

    file_id = message.video_note.file_id

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.video = file_id
        await session.commit()

    # Скачиваем для webapp
    await _save_video_to_cache(message.bot, file_id)

    await state.clear()
    await message.answer("✅ Видеокружка сохранена")
    fake_cb = type('obj', (object,), {'data': f'edit_emp_video:{employee_id}', 'message': message, 'from_user': message.from_user, 'answer': lambda *a, **kw: None})()
    await edit_emp_video_menu(fake_cb, state)


@router.callback_query(F.data.startswith("emp_view_video:"))
async def emp_view_video(callback: CallbackQuery):
    """Просмотр видео зала"""
    employee_id = int(callback.data.split(":")[1])
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)

    if not employee.video:
        await callback.answer("Нет видео", show_alert=True)
        return

    if employee.video.startswith("video_note:"):
        fid = employee.video[len("video_note:"):]
        await callback.message.answer_video_note(fid)
    else:
        await callback.message.answer_video(employee.video, caption=f"🎥 {employee.name}")
    await callback.answer()


@router.callback_query(F.data.startswith("emp_clear_video:"))
async def emp_clear_video(callback: CallbackQuery, state: FSMContext):
    """Удаление видео зала"""
    employee_id = int(callback.data.split(":")[1])
    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        employee.video = None
        await session.commit()

    await callback.answer("✅ Видео удалено", show_alert=True)
    fake_cb = type('obj', (object,), {'data': f'edit_emp_video:{employee_id}', 'message': callback.message, 'from_user': callback.from_user, 'answer': lambda *a, **kw: None})()
    await edit_emp_video_menu(fake_cb, state)


# ===== ТАРИФЫ ПО ВРЕМЕНИ (АРЕНДА ПОМЕЩЕНИЙ) =====

@router.callback_query(F.data.startswith("venue_time_pricing:"))
async def venue_time_pricing_list(callback: CallbackQuery):
    """Список тарифов по времени для помещения"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id, get_employee_time_pricing
    async with async_session_maker() as session:
        employee = await get_employee_by_id(session, employee_id)
        rules = await get_employee_time_pricing(session, employee_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    text = f"💰 <b>Тарифы по времени</b>\n\n🏠 Зал: {employee.name}\n\n"

    if rules:
        for rule in rules:
            time_from_str = rule.time_from.strftime("%H:%M")
            time_to_str = rule.time_to.strftime("%H:%M")
            cross = " (через полночь)" if rule.time_to < rule.time_from else ""
            dt_labels = {"all": "", "weekday": " (будни)", "weekend": " (вых.)"}
            dt_label = dt_labels.get(getattr(rule, 'day_type', 'all') or 'all', '')
            text += f"🕐 {time_from_str}-{time_to_str}{cross}{dt_label}: {rule.price_per_hour}₽/ч\n"
            builder.button(
                text=f"🗑 {time_from_str}-{time_to_str} ({rule.price_per_hour}₽/ч)",
                callback_data=f"del_time_pricing:{rule.id}:{employee_id}"
            )
    else:
        text += "<i>Тарифы не заданы. Используется фиксированная цена услуги.</i>\n"

    text += "\n<i>Нажмите на тариф для удаления</i>"

    builder.button(text="➕ Добавить тариф", callback_data=f"add_time_pricing:{employee_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_employee:{employee_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("add_time_pricing:"))
async def venue_time_pricing_add(callback: CallbackQuery, state: FSMContext):
    """Начало добавления тарифа — запрос времени начала"""
    employee_id = int(callback.data.split(":")[1])
    await state.update_data(time_pricing_employee_id=employee_id)
    await state.set_state(VenueTimePricingStates.entering_time_from)

    await callback.message.edit_text(
        "🕐 Введите <b>время начала</b> тарифа в формате ЧЧ:ММ\n\n"
        "Например: <code>09:00</code>",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(VenueTimePricingStates.entering_time_from)
async def venue_time_pricing_time_from(message: Message, state: FSMContext):
    """Обработка ввода времени начала тарифа"""
    text = message.text.strip()
    try:
        from datetime import time as dt_time
        parts = text.split(":")
        t = dt_time(hour=int(parts[0]), minute=int(parts[1]))
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат. Введите время в формате ЧЧ:ММ\n"
            "Например: <code>09:00</code>",
            parse_mode="HTML"
        )
        return

    await state.update_data(time_pricing_from=text)
    await state.set_state(VenueTimePricingStates.entering_time_to)

    await message.answer(
        f"✅ Начало: {text}\n\n"
        "🕐 Введите <b>время окончания</b> тарифа в формате ЧЧ:ММ\n\n"
        "Например: <code>18:00</code>",
        parse_mode="HTML"
    )


@router.message(VenueTimePricingStates.entering_time_to)
async def venue_time_pricing_time_to(message: Message, state: FSMContext):
    """Обработка ввода времени окончания тарифа"""
    text = message.text.strip()
    try:
        from datetime import time as dt_time
        parts = text.split(":")
        t = dt_time(hour=int(parts[0]), minute=int(parts[1]))
    except (ValueError, IndexError):
        await message.answer(
            "❌ Неверный формат. Введите время в формате ЧЧ:ММ\n"
            "Например: <code>18:00</code>",
            parse_mode="HTML"
        )
        return

    data = await state.get_data()
    time_from_str = data['time_pricing_from']
    from datetime import time as dt_time
    from_parts = time_from_str.split(":")
    time_from = dt_time(hour=int(from_parts[0]), minute=int(from_parts[1]))
    to_parts = text.split(":")
    time_to = dt_time(hour=int(to_parts[0]), minute=int(to_parts[1]))

    if time_to == time_from:
        await message.answer(
            "❌ Время начала и окончания совпадают. Введите другое время окончания:\n"
            f"Начало: {time_from_str}.",
            parse_mode="HTML"
        )
        return

    await state.update_data(time_pricing_to=text)
    await state.set_state(VenueTimePricingStates.entering_price)

    cross_midnight = " (через полночь)" if time_to < time_from else ""
    await message.answer(
        f"✅ Период: {time_from_str}-{text}{cross_midnight}\n\n"
        "💰 Введите <b>цену за час</b> аренды в рублях\n\n"
        "Например: <code>1500</code>",
        parse_mode="HTML"
    )


@router.message(VenueTimePricingStates.entering_price)
async def venue_time_pricing_price(message: Message, state: FSMContext):
    """Обработка ввода цены — переход к выбору дня недели"""
    text = message.text.strip()
    try:
        price = int(text)
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "❌ Введите корректную цену (целое число больше 0)\n"
            "Например: <code>1500</code>",
            parse_mode="HTML"
        )
        return

    await state.update_data(time_pricing_price=price)
    await state.set_state(VenueTimePricingStates.choosing_day_type)

    data = await state.get_data()
    time_from_str = data['time_pricing_from']
    time_to_str = data['time_pricing_to']

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Все дни", callback_data="tp_day_type:all")
    builder.button(text="🏢 Будни (Пн-Пт)", callback_data="tp_day_type:weekday")
    builder.button(text="🎉 Выходные (Сб-Вс)", callback_data="tp_day_type:weekend")
    builder.adjust(1)

    await message.answer(
        f"✅ Тариф: {time_from_str}-{time_to_str}, {price}₽/ч\n\n"
        "📅 Для каких дней действует этот тариф?",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("tp_day_type:"), VenueTimePricingStates.choosing_day_type)
async def venue_time_pricing_day_type(callback: CallbackQuery, state: FSMContext):
    """Выбор типа дня и создание тарифа"""
    day_type = callback.data.split(":")[1]  # all, weekday, weekend

    data = await state.get_data()
    employee_id = data['time_pricing_employee_id']
    time_from_str = data['time_pricing_from']
    time_to_str = data['time_pricing_to']
    price = data['time_pricing_price']

    from datetime import time as dt_time
    from_parts = time_from_str.split(":")
    to_parts = time_to_str.split(":")
    time_from = dt_time(hour=int(from_parts[0]), minute=int(from_parts[1]))
    time_to = dt_time(hour=int(to_parts[0]), minute=int(to_parts[1]))

    from database.crud import create_venue_time_pricing, get_employee_by_id, get_employee_time_pricing
    async with async_session_maker() as session:
        await create_venue_time_pricing(session, employee_id, time_from, time_to, price, day_type=day_type)
        employee = await get_employee_by_id(session, employee_id)
        rules = await get_employee_time_pricing(session, employee_id)

    await state.clear()

    day_type_labels = {"all": "", "weekday": " (будни)", "weekend": " (вых.)"}

    # Показываем обновлённый список тарифов
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    result_text = f"✅ Тариф добавлен!\n\n💰 <b>Тарифы по времени</b>\n\n🏠 Зал: {employee.name}\n\n"

    for rule in rules:
        tf = rule.time_from.strftime("%H:%M")
        tt = rule.time_to.strftime("%H:%M")
        cross = " (через полночь)" if rule.time_to < rule.time_from else ""
        dt_label = day_type_labels.get(getattr(rule, 'day_type', 'all') or 'all', '')
        result_text += f"🕐 {tf}-{tt}{cross}{dt_label}: {rule.price_per_hour}₽/ч\n"
        builder.button(
            text=f"🗑 {tf}-{tt} ({rule.price_per_hour}₽/ч)",
            callback_data=f"del_time_pricing:{rule.id}:{employee_id}"
        )

    result_text += "\n<i>Нажмите на тариф для удаления</i>"

    builder.button(text="➕ Добавить тариф", callback_data=f"add_time_pricing:{employee_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_employee:{employee_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


@router.callback_query(F.data.startswith("del_time_pricing:"))
async def venue_time_pricing_delete(callback: CallbackQuery):
    """Удаление тарифа по времени"""
    parts = callback.data.split(":")
    pricing_id = int(parts[1])
    employee_id = int(parts[2])

    from database.crud import delete_venue_time_pricing, get_employee_by_id, get_employee_time_pricing
    async with async_session_maker() as session:
        await delete_venue_time_pricing(session, pricing_id)
        employee = await get_employee_by_id(session, employee_id)
        rules = await get_employee_time_pricing(session, employee_id)

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()

    text = f"🗑 Тариф удалён!\n\n💰 <b>Тарифы по времени</b>\n\n🏠 Зал: {employee.name}\n\n"

    dt_labels = {"all": "", "weekday": " (будни)", "weekend": " (вых.)"}
    if rules:
        for rule in rules:
            tf = rule.time_from.strftime("%H:%M")
            tt = rule.time_to.strftime("%H:%M")
            cross = " (через полночь)" if rule.time_to < rule.time_from else ""
            dt_label = dt_labels.get(getattr(rule, 'day_type', 'all') or 'all', '')
            text += f"🕐 {tf}-{tt}{cross}{dt_label}: {rule.price_per_hour}₽/ч\n"
            builder.button(
                text=f"🗑 {tf}-{tt} ({rule.price_per_hour}₽/ч)",
                callback_data=f"del_time_pricing:{rule.id}:{employee_id}"
            )
    else:
        text += "<i>Тарифы не заданы. Используется фиксированная цена услуги.</i>\n"

    text += "\n<i>Нажмите на тариф для удаления</i>"

    builder.button(text="➕ Добавить тариф", callback_data=f"add_time_pricing:{employee_id}")
    builder.button(text="◀️ Назад", callback_data=f"edit_employee:{employee_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
    await callback.answer()


# ===== ДЕЖУРНЫЕ АДМИНИСТРАТОРЫ =====

@router.callback_query(F.data.startswith("duty_admins:"))
async def show_duty_admins(callback: CallbackQuery, state: FSMContext):
    """Показать список дежурных администраторов"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        employees = await get_business_employees(session, business_id)
        admins = [e for e in employees if e.role in ("administrator", "master_and_admin") and e.telegram_id]

        from datetime import date as date_cls
        today = date_cls.today()

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()

        text = "📋 <b>Дежурные администраторы сегодня</b>\n\n"
        if admins:
            text += "Нажмите на сотрудника, чтобы отметить/снять дежурство:\n"
            for admin in admins:
                is_on_duty = (admin.on_duty_date == today)
                status = "✅" if is_on_duty else "◻️"
                builder.button(
                    text=f"{status} {admin.name}",
                    callback_data=f"toggle_duty:{admin.id}:{business_id}"
                )
        else:
            text += "Администраторы с Telegram не найдены"

        builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
        builder.adjust(1)

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await callback.answer()


@router.callback_query(F.data.startswith("toggle_duty:"))
async def toggle_duty_admin(callback: CallbackQuery, state: FSMContext):
    """Переключить дежурство администратора"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    business_id = int(parts[2])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        from sqlalchemy import select as sa_select_duty
        from database.models import Employee as EmployeeModelDuty
        from datetime import date as date_cls_duty
        today = date_cls_duty.today()

        result = await session.execute(
            sa_select_duty(EmployeeModelDuty).where(EmployeeModelDuty.id == employee_id)
        )
        employee = result.scalar_one_or_none()

        if not employee:
            await callback.answer("Сотрудник не найден", show_alert=True)
            return

        if employee.on_duty_date == today:
            employee.on_duty_date = None
            status_msg = f"{employee.name}: дежурство снято"
        else:
            employee.on_duty_date = today
            status_msg = f"{employee.name}: дежурит сегодня"

        await session.commit()
        await callback.answer(status_msg)

        # Refresh list
        employees = await get_business_employees(session, business_id)
        admins = [e for e in employees if e.role in ("administrator", "master_and_admin") and e.telegram_id]

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()

        text = "📋 <b>Дежурные администраторы сегодня</b>\n\n"
        if admins:
            text += "Нажмите на сотрудника, чтобы отметить/снять дежурство:\n"
            for admin in admins:
                is_on_duty = (admin.on_duty_date == today)
                status = "✅" if is_on_duty else "◻️"
                builder.button(
                    text=f"{status} {admin.name}",
                    callback_data=f"toggle_duty:{admin.id}:{business_id}"
                )
        else:
            text += "Администраторы с Telegram не найдены"

        builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
        builder.adjust(1)

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())


# ===== ГРАФИК ДЕЖУРСТВ (МНОГОДНЕВНЫЙ) =====

@router.callback_query(F.data.startswith("duty_schedule:"))
async def show_duty_schedule(callback: CallbackQuery, state: FSMContext):
    """Показать список сотрудников для настройки графика дежурств"""
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        employees = await get_business_employees(session, business_id)
        admins = [e for e in employees if e.role in ("administrator", "master_and_admin") and e.telegram_id]

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()

        text = "📅 <b>График дежурств</b>\n\nВыберите сотрудника для настройки графика:\n"

        if admins:
            for admin in admins:
                duty_count = 0
                ds = getattr(admin, 'duty_schedule', None)
                if ds:
                    try:
                        import json as _json_ds
                        dates = _json_ds.loads(ds) if isinstance(ds, str) else ds
                        from datetime import date as _d_ds
                        today_ds = _d_ds.today()
                        duty_count = len([d for d in dates if d >= today_ds.isoformat()])
                    except Exception:
                        pass
                suffix = f" ({duty_count} дн.)" if duty_count > 0 else ""
                builder.button(
                    text=f"📅 {admin.name}{suffix}",
                    callback_data=f"duty_cal:{admin.id}:{business_id}:0"
                )
        else:
            text += "\nАдминистраторы с Telegram не найдены"

        builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
        builder.adjust(1)

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await callback.answer()


@router.callback_query(F.data.startswith("duty_cal:"))
async def show_duty_calendar(callback: CallbackQuery, state: FSMContext):
    """Показать календарь дежурств для конкретного сотрудника"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    business_id = int(parts[2])
    week_offset = int(parts[3])

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        from sqlalchemy import select as sa_sel_cal
        from database.models import Employee as EmpModelCal
        result = await session.execute(sa_sel_cal(EmpModelCal).where(EmpModelCal.id == employee_id))
        employee = result.scalar_one_or_none()

        if not employee:
            await callback.answer("Сотрудник не найден", show_alert=True)
            return

        import json as _json_cal
        from datetime import date as _date_cal, timedelta as _td_cal

        duty_dates = set()
        ds = getattr(employee, 'duty_schedule', None)
        if ds:
            try:
                parsed = _json_cal.loads(ds) if isinstance(ds, str) else ds
                duty_dates = set(parsed) if parsed else set()
            except Exception:
                pass

        today = _date_cal.today()
        week_start = today - _td_cal(days=today.weekday()) + _td_cal(weeks=week_offset)
        days = [week_start + _td_cal(days=i) for i in range(14)]

        DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

        text = f"📅 <b>График дежурств: {employee.name}</b>\n\n"
        text += f"📆 {days[0].strftime('%d.%m')} — {days[13].strftime('%d.%m.%Y')}\n"
        text += "Нажмите на день чтобы вкл/выкл дежурство:\n"

        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()

        for day in days:
            day_str = day.isoformat()
            is_duty = day_str in duty_dates
            day_name = DAY_NAMES[day.weekday()]
            icon = "✅" if is_duty else "◻️"
            label = f"{icon} {day_name} {day.strftime('%d.%m')}"
            builder.button(
                text=label,
                callback_data=f"duty_tog:{employee_id}:{business_id}:{week_offset}:{day_str}"
            )

        if week_offset > 0:
            builder.button(text="⬅️ Пред. 2 нед.", callback_data=f"duty_cal:{employee_id}:{business_id}:{week_offset - 2}")
        builder.button(text="➡️ След. 2 нед.", callback_data=f"duty_cal:{employee_id}:{business_id}:{week_offset + 2}")

        builder.button(text="◀️ К списку", callback_data=f"duty_schedule:{business_id}")
        if week_offset > 0:
            builder.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 2, 1)
        else:
            builder.adjust(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1)

        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=builder.as_markup())
        await callback.answer()


@router.callback_query(F.data.startswith("duty_tog:"))
async def toggle_duty_date(callback: CallbackQuery, state: FSMContext):
    """Переключить дежурство на конкретную дату"""
    parts = callback.data.split(":")
    employee_id = int(parts[1])
    business_id = int(parts[2])
    week_offset = int(parts[3])
    date_str = parts[4]

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if not await is_business_owner(session, business_id, user.id):
            await callback.answer("Недостаточно прав", show_alert=True)
            return

        from sqlalchemy import select as sa_sel_tog
        from database.models import Employee as EmpModelTog
        result = await session.execute(sa_sel_tog(EmpModelTog).where(EmpModelTog.id == employee_id))
        employee = result.scalar_one_or_none()

        if not employee:
            await callback.answer("Сотрудник не найден", show_alert=True)
            return

        import json as _json_tog
        duty_dates = set()
        ds = getattr(employee, 'duty_schedule', None)
        if ds:
            try:
                parsed = _json_tog.loads(ds) if isinstance(ds, str) else ds
                duty_dates = set(parsed) if parsed else set()
            except Exception:
                pass

        if date_str in duty_dates:
            duty_dates.discard(date_str)
            status_msg = f"❌ {date_str}: дежурство снято"
        else:
            duty_dates.add(date_str)
            status_msg = f"✅ {date_str}: дежурство добавлено"

        employee.duty_schedule = _json_tog.dumps(sorted(list(duty_dates)))
        await session.commit()
        await callback.answer(status_msg)

    callback.data = f"duty_cal:{employee_id}:{business_id}:{week_offset}"
    await show_duty_calendar(callback, state)
