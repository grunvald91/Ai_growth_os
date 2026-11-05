"""
Обработчики общих команд (/start, /help и т.д.)
"""
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database import async_session_maker
from database.crud import (
    get_or_create_user, get_user_businesses, get_employee_by_telegram_id,
    get_administrator_by_telegram_id, get_master_by_telegram_id,
    get_administrators_by_telegram_id
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.client_kb import get_main_menu_keyboard, get_employee_mode_selection_keyboard
from bot.keyboards.admin_kb import get_admin_menu_keyboard
from bot.keyboards import master_kb
from bot.keyboards.administrator_kb import get_administrator_menu_keyboard
from bot.config import ADMIN_IDS
from bot.states.common import ReportProblemStates
from bot.services.mlm_sync import can_be_business_owner

SUPER_ADMIN_ID = 437862772

router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    """Обработка команды /start с поддержкой deep link для прямого бронирования"""
    import logging
    logger = logging.getLogger(__name__)

    # Очищаем состояние FSM при старте
    await state.clear()

    # Проверяем deep link параметр (например /start book_123)
    args = message.text.split(maxsplit=1)
    deep_link_param = args[1] if len(args) > 1 else None

    logger.info(f"cmd_start: message.text='{message.text}', args={args}, deep_link_param={deep_link_param}")

    # Если есть deep link для бронирования - перенаправляем
    if deep_link_param and deep_link_param.startswith("book_"):
        try:
            business_id = int(deep_link_param.replace("book_", ""))
            logger.info(f"Deep link detected: business_id={business_id}")
            await start_direct_booking(message, state, business_id)
            return
        except (ValueError, Exception) as e:
            logger.error(f"Deep link error: {e}")
            pass  # Если ошибка - показываем обычное меню

    # Deep link из inline-визитки: /start biz_123
    if deep_link_param and deep_link_param.startswith("biz_"):
        try:
            business_id = int(deep_link_param.replace("biz_", ""))
            logger.info(f"Inline card deep link: business_id={business_id}")
            await start_direct_booking(message, state, business_id)
            return
        except (ValueError, Exception) as e:
            logger.error(f"Inline card deep link error: {e}")
            pass

    # Deep link "подписка на уведомления о брони, созданной из webapp"
    # /start notify_<token> — привязывает анонимного web-клиента к Telegram
    if deep_link_param and deep_link_param.startswith("notify_"):
        try:
            token = deep_link_param.replace("notify_", "", 1)
            logger.info(f"notify deep link: token={token[:6]}...")
            await handle_notify_deeplink(message, token)
            return
        except Exception as e:
            logger.error(f"notify deep link error: {e}")
            pass

    async with async_session_maker() as session:
        # Создаем или получаем пользователя
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        # Проверяем, является ли пользователь владельцем бизнеса
        businesses = await get_user_businesses(session, user.id)

        # Проверяем, является ли пользователь сотрудником (мастер или администратор)
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    # Проверяем, является ли пользователь владельцем
    # 1. В ADMIN_IDS, или
    # 2. Имеет бизнесы в booking_bot, или
    # 3. Имеет активную подписку с ролью business/both в MLM боте
    mlm_owner = await can_be_business_owner(message.from_user.id)
    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
    is_employee = employee is not None
    is_super_admin = message.from_user.id == 437862772

    welcome_text = f"""
Привет, {message.from_user.first_name}! 👋

Я бот для онлайн-записи на услуги.

Здесь вы можете:
📅 Записаться к специалистам
📋 Управлять своими записями
"""

    # Всегда начинаем с режима клиента, показываем доступные переключения
    await state.update_data(current_mode="client")

    # Добавляем информацию о доступных режимах
    if is_owner:
        if businesses:
            welcome_text += f"\n🏢 У вас {len(businesses)} бизнес(-ов) — доступен режим владельца"
        else:
            welcome_text += "\n🏢 Вы можете создать свой бизнес — доступен режим владельца"

    if is_employee:
        welcome_text += f"\n💼 Вы сотрудник в {employee.business.name} — доступен режим сотрудника"

    await message.answer(
        welcome_text,
        reply_markup=get_main_menu_keyboard(
            show_owner_button=is_owner,
            show_employee_button=is_employee
        )
    )


async def start_direct_booking(message: Message, state: FSMContext, business_id: int):
    """Начать прямое бронирование для конкретного бизнеса (через QR/deep link/inline).
    Показывает карточку бизнеса с кнопкой «Продолжить запись» — тот же экран,
    что при обычном выборе бизнеса клиентом (client.py → business_chosen)."""
    import json
    import logging
    from urllib.parse import quote
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    from database.crud import get_business_by_id, get_business_services
    from bot.states.booking import BookingStates
    from bot.utils.business_types import is_venue_rental_type

    logger = logging.getLogger(__name__)
    logger.info(f"start_direct_booking called with business_id={business_id}")

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        business = await get_business_by_id(session, business_id)

        if not business or not business.is_active:
            await message.answer(
                "❌ К сожалению, этот бизнес не найден или временно недоступен.",
                reply_markup=get_main_menu_keyboard()
            )
            return

        services = await get_business_services(session, business_id)

    if not services:
        await message.answer(
            f"❌ В «{business.name}» пока нет доступных услуг для записи.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # Сохраняем в state
    await state.update_data(
        business_id=business_id,
        business_name=business.name,
        business_type=business.business_type,
        current_mode="client"
    )

    # Формируем карточку бизнеса (аналогично client.py → business_chosen)
    info_text = f"🏢 <b>{business.name}</b>\n\n"

    if business.description:
        info_text += f"📝 {business.description}\n\n"

    if business.phone:
        info_text += f"📞 Телефон: {business.phone}\n"

    if business.address:
        encoded_address = quote(business.address)
        yandex_maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        info_text += f'📍 Адрес: <a href="{yandex_maps_url}">{business.address}</a>\n'

    if business.working_hours_start and business.working_hours_end:
        info_text += f"⏰ Часы работы: {business.working_hours_start.strftime('%H:%M')} - {business.working_hours_end.strftime('%H:%M')}\n"

    if business.break_start and business.break_end:
        info_text += f"☕ Перерыв: {business.break_start.strftime('%H:%M')} - {business.break_end.strftime('%H:%M')}\n"

    # Кнопки — фото, видео, как добраться, продолжить запись
    has_photos = False
    if business.photos:
        try:
            photos_data = json.loads(business.photos)
            has_photos = len(photos_data) > 0
        except Exception:
            pass
    has_directions = business.directions is not None and business.directions.strip() != ""

    builder = InlineKeyboardBuilder()
    if has_photos:
        builder.button(text="📷 Фото заведения", callback_data=f"booking_view_photos:{business_id}")
    if getattr(business, 'video', None):
        builder.button(text="🎬 Видеообзор", callback_data=f"business_video:{business_id}")
    if has_directions:
        builder.button(text="🗺 Как добраться", callback_data=f"booking_view_directions:{business_id}")
    if is_venue_rental_type(business.business_type):
        builder.button(text="👀 Посмотреть залы", callback_data=f"view_rooms:{business_id}")

    builder.button(text="➡️ Продолжить запись", callback_data=f"continue_booking:{business_id}")
    builder.adjust(1)

    await message.answer(
        info_text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=builder.as_markup()
    )
    await state.set_state(BookingStates.choosing_business)


async def handle_notify_deeplink(message: Message, token: str):
    """Привязать анонимного web-клиента (создавшего бронь через webapp без
    логина в боте) к этому Telegram-аккаунту, чтобы он мог получать
    уведомления о статусе предоплаты / подтверждении / напоминания."""
    import logging
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from database.models import Booking, User

    logger = logging.getLogger(__name__)

    if not token or len(token) < 6 or len(token) > 64:
        await message.answer("❌ Неверная или просроченная ссылка для подписки.")
        return

    async with async_session_maker() as session:
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.client),
                selectinload(Booking.employee),
            )
            .where(Booking.notify_token == token)
            .limit(1)
        )
        booking = result.scalar_one_or_none()

        if not booking:
            await message.answer(
                "❌ Бронь не найдена.\n\n"
                "Возможно ссылка устарела или относится к старой брони.",
            )
            return

        anon_client = booking.client
        tg_id = message.from_user.id

        # Если у брони клиент-аноним (отрицательный синтетический telegram_id),
        # подменяем его на реальный — если этот реальный пользователь ещё не существует.
        if anon_client and anon_client.telegram_id is not None and anon_client.telegram_id < 0:
            existing = await session.execute(
                select(User).where(User.telegram_id == tg_id)
            )
            existing_user = existing.scalar_one_or_none()
            if existing_user:
                # Переключаем все брони/контакты с anon_client на existing_user
                from sqlalchemy import update
                await session.execute(
                    update(Booking).where(Booking.client_id == anon_client.id).values(client_id=existing_user.id)
                )
                # Сохраним телефон если был у анона
                if anon_client.phone and not existing_user.phone:
                    existing_user.phone = anon_client.phone
                await session.flush()
            else:
                anon_client.telegram_id = tg_id
                if message.from_user.username:
                    anon_client.username = message.from_user.username
                if message.from_user.full_name:
                    anon_client.full_name = message.from_user.full_name
            await session.commit()
            await session.refresh(booking)

        # Снова подгружаем актуального клиента после возможного переключения
        result = await session.execute(
            select(Booking)
            .options(
                selectinload(Booking.business),
                selectinload(Booking.service),
                selectinload(Booking.client),
                selectinload(Booking.employee),
            )
            .where(Booking.id == booking.id)
            .limit(1)
        )
        booking = result.scalar_one_or_none()

    # Сформируем краткое инфо о брони
    status_map = {
        "pending": "⏳ Ожидает подтверждения",
        "pending_prepayment": "💳 Ожидает предоплату",
        "prepayment_sent": "🕓 Чек проверяется владельцем",
        "confirmed": "✅ Подтверждена",
        "completed": "✔️ Завершена",
        "cancelled": "❌ Отменена",
    }
    status_label = status_map.get(booking.status, booking.status)
    date_str = booking.booking_date.strftime("%d.%m.%Y")
    time_str = booking.booking_time.strftime("%H:%M")
    emp_line = f"\n👤 Специалист: {booking.employee.name}" if booking.employee else ""
    svc_line = booking.service.name if booking.service else ""
    biz_line = booking.business.name if booking.business else ""

    text = (
        "🔔 <b>Вы подписаны на уведомления по брони</b>\n\n"
        f"🏢 {biz_line}\n"
        f"💼 {svc_line}{emp_line}\n"
        f"📅 {date_str} в {time_str}\n\n"
        f"<b>Статус:</b> {status_label}\n\n"
        "Здесь вы получите сообщение, когда владелец подтвердит запись или предоплату, "
        "и напоминания за 24 ч и 2 ч до визита."
    )
    await message.answer(text, parse_mode="HTML", reply_markup=get_main_menu_keyboard())


@router.message(Command("help"))
@router.message(F.text == "ℹ️ Помощь")
async def cmd_help(message: Message, state: FSMContext):
    """Обработка команды /help с контекстной справкой для каждого режима"""

    # Получаем роли пользователя
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    is_admin = message.from_user.id in ADMIN_IDS
    mlm_owner = await can_be_business_owner(message.from_user.id)
    show_admin = is_admin or len(businesses) > 0 or mlm_owner
    show_master = employee is not None
    is_super_admin = message.from_user.id == 437862772

    # Получаем текущий режим из состояния
    state_data = await state.get_data()
    current_mode = state_data.get("current_mode", "client")

    # Справка для режима клиента
    client_help = f"""
📖 <b>Помощь: Режим клиента</b>

<b>Основные функции:</b>
📅 <b>Записаться</b> - создать новую запись на услугу
   • Выберите бизнес из списка
   • Выберите нужную услугу
   • Выберите мастера (или "Любой мастер")
   • Выберите удобную дату и время
   • Подтвердите запись

🔄 <b>Повторить запись</b> - быстро повторить предыдущую запись
   • Выберите из истории записей
   • Измените только дату и время

📋 <b>Мои записи</b> - посмотреть и управлять записями
   • Просмотр предстоящих записей
   • Отмена записи при необходимости

<b>Команды:</b>
/start - Перезапустить бота
/help - Показать эту справку
/cancel - Отменить текущее действие

<b>Ваш Telegram ID:</b> <code>{message.from_user.id}</code>
"""

    # Справка для режима владельца
    admin_help = f"""
📖 <b>Помощь: Режим владельца</b>

<b>Управление бизнесом:</b>
🏢 <b>Мои бизнесы</b> - список всех ваших бизнесов
   • Редактирование информации о бизнесе
   • Управление услугами и сотрудниками
   • Включение/выключение бизнеса

➕ <b>Добавить бизнес</b> - создать новый бизнес
   • Выберите тип бизнеса
   • Укажите название и описание
   • Добавьте адрес и контакты
   • Настройте время работы

<b>Управление услугами:</b>
   • Добавляйте услуги через меню бизнеса
   • Укажите название, длительность и цену
   • Включайте/выключайте услуги

<b>Управление сотрудниками:</b>
   • Добавляйте мастеров через меню бизнеса
   • Укажите имя и специализацию
   • Добавьте Telegram ID для доступа к панели мастера
   • Настройте интервал записи

📊 <b>Записи</b> - просмотр всех записей клиентов
   • Фильтрация по датам
   • Подтверждение, завершение, отмена записей

<b>Команды:</b>
/start - Перезапустить бота
/help - Показать эту справку
/cancel - Отменить текущее действие

<b>Ваш Telegram ID:</b> <code>{message.from_user.id}</code>
"""

    # Справка для режима мастера
    master_help = f"""
📖 <b>Помощь: Режим мастера</b>

<b>Ваше расписание:</b>
📅 <b>Моё расписание</b> - просмотр записей
   • Записи на сегодня, завтра, неделю
   • Информация о клиентах
   • Завершение и отмена записей

<b>Работа с клиентами:</b>
➕ <b>Записать клиента</b> - ручная запись
   • Выберите услугу
   • Введите имя и телефон клиента
   • Выберите дату и время
   • Подтвердите запись

👤 <b>История клиента</b> - при просмотре записи
   • Посмотреть предыдущие записи клиента
   • Информация о посещениях

📊 <b>Моя статистика</b> - показатели работы
   • Количество записей
   • Выручка за период

<b>Статусы записей:</b>
⏳ Ожидает подтверждения
✅ Подтверждена
✔️ Завершена
❌ Отменена

<b>Команды:</b>
/start - Перезапустить бота
/help - Показать эту справку
/cancel - Отменить текущее действие

<b>Ваш Telegram ID:</b> <code>{message.from_user.id}</code>
"""

    # Справка для режима администратора (сотрудника)
    administrator_help = f"""
📖 <b>Помощь: Режим администратора</b>

<b>Управление записями:</b>
📋 <b>Все записи</b> - просмотр всех записей бизнеса
   • Фильтрация по датам
   • Просмотр деталей записи

➕ <b>Записать клиента</b> - создать запись вручную
   • Выберите мастера и услугу
   • Введите данные клиента
   • Выберите дату и время

❌ <b>Отменить запись</b> - отмена записи клиента
   • Выберите запись из списка
   • Подтвердите отмену

👥 <b>Клиенты</b> - база клиентов
📊 <b>Статистика</b> - показатели бизнеса

<b>Ваш Telegram ID:</b> <code>{message.from_user.id}</code>
"""

    is_owner = is_admin or len(businesses) > 0
    is_employee = employee is not None

    # Выбираем справку и клавиатуру в зависимости от режима
    if current_mode == "owner":
        help_text = admin_help
        # Добавляем информацию для супер-админа
        if is_super_admin:
            help_text += """
<b>Супер-админ:</b>
👥 <b>Управление владельцами</b>
   • Добавление новых владельцев
   • Удаление владельцев
"""
        reply_keyboard = get_admin_menu_keyboard(show_employee_button=is_employee, is_super_admin=is_super_admin)
    elif current_mode == "master":
        help_text = master_help
        reply_keyboard = master_kb.get_master_menu_keyboard(show_owner_button=is_owner)
    elif current_mode == "administrator":
        help_text = administrator_help
        # Получаем business_type для корректной клавиатуры
        admin_business_type = None
        admin_employee_id = state_data.get("admin_employee_id")
        if admin_employee_id:
            from database.crud import get_employee_by_id
            async with async_session_maker() as session:
                admin_emp = await get_employee_by_id(session, admin_employee_id)
                if admin_emp and admin_emp.business:
                    admin_business_type = admin_emp.business.business_type
        reply_keyboard = get_administrator_menu_keyboard(show_owner_button=is_owner, business_type=admin_business_type)
    else:
        help_text = client_help
        reply_keyboard = get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=is_employee)

    # Инлайн-кнопка "Сообщить о проблеме"
    inline_builder = InlineKeyboardBuilder()
    inline_builder.button(text="🐛 Сообщить о проблеме", callback_data="report_problem")

    await message.answer(help_text, parse_mode="HTML", reply_markup=reply_keyboard)
    await message.answer(
        "Если у вас возникли проблемы с ботом, нажмите кнопку ниже:",
        reply_markup=inline_builder.as_markup()
    )


@router.message(Command("cancel"))
@router.message(F.text.in_(["❌ Отмена", "Отмена"]))
async def cmd_cancel(message: Message, state: FSMContext):
    """Отмена текущего действия"""
    current_state = await state.get_state()

    # Получаем роли пользователя для кнопок переключения
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    mlm_owner = await can_be_business_owner(message.from_user.id)
    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
    is_employee = employee is not None

    reply_keyboard = get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=is_employee)

    if current_state is None:
        await message.answer(
            "😊 Нечего отменять!",
            reply_markup=reply_keyboard
        )
        return

    await state.clear()
    await message.answer(
        "✅ Действие отменено",
        reply_markup=reply_keyboard
    )


@router.message(F.text == "👤 Режим клиента")
async def switch_to_client_mode(message: Message, state: FSMContext):
    """Переключение в режим клиента"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    mlm_owner = await can_be_business_owner(message.from_user.id)
    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
    is_employee = employee is not None

    # Сохраняем текущий режим
    await state.update_data(current_mode="client")

    await message.answer(
        "🏠 Добро пожаловать! 😊\n\nВыберите действие в меню ниже ⬇️",
        reply_markup=get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=is_employee)
    )


@router.message(F.text == "🏢 Режим владельца")
async def switch_to_owner_mode(message: Message, state: FSMContext):
    """Переключение в режим владельца"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)
        employee = await get_employee_by_telegram_id(session, message.from_user.id)

    is_admin = message.from_user.id in ADMIN_IDS
    mlm_owner = await can_be_business_owner(message.from_user.id)
    is_owner = is_admin or len(businesses) > 0 or mlm_owner
    is_employee = employee is not None

    # Разрешаем доступ если есть бизнесы ИЛИ пользователь в ADMIN_IDS
    if not is_owner:
        await message.answer(
            "🔒 У вас нет доступа к панели владельца",
            reply_markup=get_main_menu_keyboard(show_owner_button=False, show_employee_button=is_employee)
        )
        return

    # Сохраняем текущий режим
    await state.update_data(current_mode="owner")

    await message.answer(
        "🏢 Панель владельца 👋\n\n"
        "Здесь вы можете управлять своими бизнесами, услугами и сотрудниками 💼",
        reply_markup=get_admin_menu_keyboard(
            show_employee_button=is_employee,
            is_super_admin=(message.from_user.id == 437862772)
        )
    )


@router.message(F.text == "💼 Режим сотрудника")
async def switch_to_employee_mode(message: Message, state: FSMContext):
    """Переключение в режим сотрудника - показывает выбор подрежима"""
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

        # Проверяем роли сотрудника
        master = await get_master_by_telegram_id(session, message.from_user.id)
        administrators = await get_administrators_by_telegram_id(session, message.from_user.id)

    mlm_owner = await can_be_business_owner(message.from_user.id)
    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
    is_master = master is not None
    is_administrator = len(administrators) > 0

    if not is_master and not is_administrator:
        await message.answer(
            "🔒 Вы не зарегистрированы как сотрудник\n\n"
            "Обратитесь к владельцу бизнеса, чтобы он добавил ваш Telegram ID в профиль сотрудника 📱",
            reply_markup=get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=False)
        )
        return

    # Сохраняем текущий режим
    await state.update_data(current_mode="employee")

    # Если только одна роль - сразу переходим в неё
    if is_master and not is_administrator:
        await state.update_data(current_mode="master")
        await message.answer(
            f"👨‍🔧 Режим мастера\n\n"
            f"🏢 Бизнес: {master.business.name}\n"
            f"💼 Специализация: {master.specialization or 'Не указана'}",
            reply_markup=master_kb.get_master_menu_keyboard(show_owner_button=is_owner)
        )
        return

    if is_administrator and not is_master:
        # Если только один бизнес - сразу входим
        if len(administrators) == 1:
            administrator = administrators[0]
            await state.update_data(
                current_mode="administrator",
                admin_business_id=administrator.business_id,
                admin_employee_id=administrator.id
            )
            await message.answer(
                f"👔 Режим администратора\n\n"
                f"🏢 Бизнес: {administrator.business.name}",
                reply_markup=get_administrator_menu_keyboard(show_owner_button=is_owner, business_type=administrator.business.business_type)
            )
            return

        # Если несколько бизнесов - показываем выбор
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        for admin in administrators:
            builder.button(
                text=f"🏢 {admin.business.name}",
                callback_data=f"select_admin_business:{admin.id}"
            )
        builder.adjust(1)

        await state.update_data(current_mode="administrator")
        await message.answer(
            "👔 <b>Режим администратора</b>\n\n"
            "Вы являетесь администратором в нескольких бизнесах.\n"
            "Выберите бизнес для работы:",
            parse_mode="HTML",
            reply_markup=builder.as_markup()
        )
        return

    # Если обе роли - показываем выбор
    await message.answer(
        "💼 <b>Режим сотрудника</b>\n\n"
        "Выберите роль для работы:",
        parse_mode="HTML",
        reply_markup=get_employee_mode_selection_keyboard(
            is_master=is_master,
            is_administrator=is_administrator,
            show_owner_button=is_owner
        )
    )


@router.message(F.text == "👨‍🔧 Режим мастера")
async def switch_to_master_mode(message: Message, state: FSMContext):
    """Переключение в режим мастера (из меню сотрудника)"""
    async with async_session_maker() as session:
        master = await get_master_by_telegram_id(session, message.from_user.id)
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    mlm_owner = await can_be_business_owner(message.from_user.id)

    if not master:
        is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
        await message.answer(
            "🔒 Вы не зарегистрированы как мастер",
            reply_markup=get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=False)
        )
        return

    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner

    # Сохраняем текущий режим
    await state.update_data(current_mode="master")

    await message.answer(
        f"👨‍🔧 Режим мастера\n\n"
        f"🏢 Бизнес: {master.business.name}\n"
        f"💼 Специализация: {master.specialization or 'Не указана'}",
        reply_markup=master_kb.get_master_menu_keyboard(show_owner_button=is_owner)
    )


@router.message(F.text == "👔 Режим администратора")
async def switch_to_administrator_mode(message: Message, state: FSMContext):
    """Переключение в режим администратора (из меню сотрудника)"""
    async with async_session_maker() as session:
        administrators = await get_administrators_by_telegram_id(session, message.from_user.id)
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    mlm_owner = await can_be_business_owner(message.from_user.id)

    if not administrators:
        is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner
        await message.answer(
            "🔒 Вы не зарегистрированы как администратор",
            reply_markup=get_main_menu_keyboard(show_owner_button=is_owner, show_employee_button=False)
        )
        return

    is_owner = message.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner

    # Если только один бизнес - сразу входим
    if len(administrators) == 1:
        administrator = administrators[0]
        await state.update_data(
            current_mode="administrator",
            admin_business_id=administrator.business_id,
            admin_employee_id=administrator.id
        )
        await message.answer(
            f"👔 Режим администратора\n\n"
            f"🏢 Бизнес: {administrator.business.name}",
            reply_markup=get_administrator_menu_keyboard(show_owner_button=is_owner, business_type=administrator.business.business_type)
        )
        return

    # Если несколько бизнесов - показываем выбор
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    for admin in administrators:
        builder.button(
            text=f"🏢 {admin.business.name}",
            callback_data=f"select_admin_business:{admin.id}"
        )
    builder.adjust(1)

    await state.update_data(current_mode="administrator")
    await message.answer(
        "👔 <b>Режим администратора</b>\n\n"
        "Вы являетесь администратором в нескольких бизнесах.\n"
        "Выберите бизнес для работы:",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )


@router.callback_query(F.data.startswith("select_admin_business:"))
async def select_admin_business(callback: CallbackQuery, state: FSMContext):
    """Выбор бизнеса для режима администратора"""
    employee_id = int(callback.data.split(":")[1])

    from database.crud import get_employee_by_id
    async with async_session_maker() as session:
        administrator = await get_employee_by_id(session, employee_id)
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    if not administrator:
        await callback.answer("❌ Ошибка: администратор не найден", show_alert=True)
        return

    mlm_owner = await can_be_business_owner(callback.from_user.id)
    is_owner = callback.from_user.id in ADMIN_IDS or len(businesses) > 0 or mlm_owner

    await state.update_data(
        admin_business_id=administrator.business_id,
        admin_employee_id=administrator.id
    )

    await callback.message.edit_text(
        f"👔 Режим администратора\n\n"
        f"🏢 Бизнес: {administrator.business.name}"
    )
    await callback.message.answer(
        "Выберите действие:",
        reply_markup=get_administrator_menu_keyboard(show_owner_button=is_owner, business_type=administrator.business.business_type)
    )
    await callback.answer()


# ===== СООБЩИТЬ О ПРОБЛЕМЕ =====

@router.callback_query(F.data == "report_problem")
async def start_report_problem(callback: CallbackQuery, state: FSMContext):
    """Начало процесса отправки сообщения о проблеме"""
    await state.set_state(ReportProblemStates.waiting_for_description)

    # Сохраняем информацию о пользователе
    await state.update_data(
        reporter_id=callback.from_user.id,
        reporter_username=callback.from_user.username,
        reporter_name=callback.from_user.full_name
    )

    cancel_builder = InlineKeyboardBuilder()
    cancel_builder.button(text="❌ Отмена", callback_data="cancel_report")

    await callback.message.edit_text(
        "🐛 <b>Сообщить о проблеме</b>\n\n"
        "Опишите проблему, с которой вы столкнулись.\n"
        "Постарайтесь указать:\n"
        "• Что вы пытались сделать\n"
        "• Что произошло\n"
        "• Какой результат ожидали\n\n"
        "Отправьте текстовое сообщение:",
        parse_mode="HTML",
        reply_markup=cancel_builder.as_markup()
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_report")
async def cancel_report(callback: CallbackQuery, state: FSMContext):
    """Отмена репорта проблемы"""
    await state.clear()
    await callback.message.edit_text("✅ Отправка сообщения отменена")
    await callback.answer()


@router.message(ReportProblemStates.waiting_for_description)
async def receive_problem_description(message: Message, state: FSMContext):
    """Получение описания проблемы"""
    if not message.text:
        await message.answer("📝 Пожалуйста, отправьте текстовое описание проблемы")
        return

    await state.update_data(problem_description=message.text)
    await state.set_state(ReportProblemStates.waiting_for_screenshot)

    screenshot_builder = InlineKeyboardBuilder()
    screenshot_builder.button(text="📤 Отправить без скриншота", callback_data="send_report_no_screenshot")
    screenshot_builder.button(text="❌ Отмена", callback_data="cancel_report")
    screenshot_builder.adjust(1)

    await message.answer(
        "📸 <b>Скриншот (опционально)</b>\n\n"
        "Если хотите, приложите скриншот проблемы.\n"
        "Или нажмите кнопку, чтобы отправить без скриншота.",
        parse_mode="HTML",
        reply_markup=screenshot_builder.as_markup()
    )


@router.callback_query(F.data == "send_report_no_screenshot", ReportProblemStates.waiting_for_screenshot)
async def send_report_without_screenshot(callback: CallbackQuery, state: FSMContext):
    """Отправка репорта без скриншота"""
    data = await state.get_data()
    await send_problem_report(callback.bot, data, screenshot_file_id=None)

    await state.clear()
    await callback.message.edit_text(
        "✅ <b>Спасибо за сообщение!</b>\n\n"
        "Ваш репорт отправлен администратору. "
        "Мы постараемся решить проблему как можно скорее.",
        parse_mode="HTML"
    )
    await callback.answer("Репорт отправлен!")


@router.message(ReportProblemStates.waiting_for_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    """Получение скриншота и отправка репорта"""
    data = await state.get_data()
    # Берём фото в лучшем качестве (последнее в списке)
    screenshot_file_id = message.photo[-1].file_id

    await send_problem_report(message.bot, data, screenshot_file_id=screenshot_file_id)

    await state.clear()
    await message.answer(
        "✅ <b>Спасибо за сообщение!</b>\n\n"
        "Ваш репорт со скриншотом отправлен администратору. "
        "Мы постараемся решить проблему как можно скорее.",
        parse_mode="HTML"
    )


@router.message(ReportProblemStates.waiting_for_screenshot)
async def invalid_screenshot(message: Message):
    """Обработка неверного формата при ожидании скриншота"""
    screenshot_builder = InlineKeyboardBuilder()
    screenshot_builder.button(text="📤 Отправить без скриншота", callback_data="send_report_no_screenshot")
    screenshot_builder.button(text="❌ Отмена", callback_data="cancel_report")
    screenshot_builder.adjust(1)

    await message.answer(
        "❌ Пожалуйста, отправьте изображение (фото/скриншот) или нажмите кнопку ниже.",
        reply_markup=screenshot_builder.as_markup()
    )


async def send_problem_report(bot, data: dict, screenshot_file_id: str = None):
    """Отправка репорта суперадмину"""
    reporter_id = data.get("reporter_id")
    reporter_username = data.get("reporter_username")
    reporter_name = data.get("reporter_name")
    description = data.get("problem_description")

    # Формируем ссылку на пользователя
    if reporter_username:
        user_link = f'@{reporter_username} (<a href="tg://user?id={reporter_id}">{reporter_name}</a>)'
    else:
        user_link = f'<a href="tg://user?id={reporter_id}">{reporter_name}</a>'

    report_text = f"""
🐛 <b>Новый репорт о проблеме</b>

👤 <b>От:</b> {user_link}
🆔 <b>ID:</b> <code>{reporter_id}</code>

📝 <b>Описание проблемы:</b>
{description}
"""

    try:
        if screenshot_file_id:
            await bot.send_photo(
                chat_id=SUPER_ADMIN_ID,
                photo=screenshot_file_id,
                caption=report_text,
                parse_mode="HTML"
            )
        else:
            await bot.send_message(
                chat_id=SUPER_ADMIN_ID,
                text=report_text,
                parse_mode="HTML"
            )
    except Exception as e:
        import logging
        logging.error(f"Error sending problem report to super admin: {e}")


@router.message(Command("weblogin"))
async def cmd_weblogin(message: Message):
    """Получить данные для входа в веб-панель (без Telegram)"""
    import hmac
    import hashlib
    from bot.config import BOT_TOKEN, WEBAPP_URL

    telegram_id = message.from_user.id

    # Проверяем, является ли пользователь владельцем бизнеса
    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=telegram_id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )
        businesses = await get_user_businesses(session, user.id)

    mlm_owner = await can_be_business_owner(telegram_id)
    is_owner = telegram_id in ADMIN_IDS or len(businesses) > 0 or mlm_owner

    if not is_owner:
        await message.answer("⚠️ Эта команда доступна только владельцам бизнеса.")
        return

    # Генерируем токен и PIN
    token = hmac.new(
        BOT_TOKEN.encode(),
        f"web_owner_{telegram_id}".encode(),
        hashlib.sha256
    ).hexdigest()

    pin_num = int(token[:8], 16) % 1000000
    pin = f"{pin_num:06d}"

    login_url = f"{WEBAPP_URL}/auth/token/{token}"

    text = f"""🔐 <b>Веб-доступ к панели управления</b>

Используйте эти данные для входа на сайте, если Telegram недоступен.

📱 <b>Ваш телефон:</b> {user.phone or '(не указан)'}
🔑 <b>PIN-код:</b> <code>{pin}</code>

Или перейдите по прямой ссылке:
{login_url}

💡 Сохраните эту ссылку в закладки!

⚠️ Не передавайте PIN и ссылку посторонним."""

    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)
