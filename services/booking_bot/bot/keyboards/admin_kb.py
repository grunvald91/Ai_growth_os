"""
Клавиатуры для админ-панели
"""
from typing import List
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Business
from bot.config import WEBAPP_URL


def get_admin_menu_keyboard(show_employee_button: bool = False, is_super_admin: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню для владельца

    Args:
        show_employee_button: показывать кнопку "Режим сотрудника"
        is_super_admin: показывать кнопку "Управление владельцами"
    """
    # Кнопка аналитики с WebApp
    analytics_button = KeyboardButton(
        text="📈 Аналитика",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/analytics") if WEBAPP_URL else None
    )

    # Кнопка расписания с WebApp
    schedule_button = KeyboardButton(
        text="📅 Расписание",
        web_app=WebAppInfo(url=f"{WEBAPP_URL}/schedule?role=owner") if WEBAPP_URL else None
    )

    buttons = [
        [KeyboardButton(text="🏢 Мои бизнесы")],
        [KeyboardButton(text="➕ Добавить бизнес"), KeyboardButton(text="📊 Записи")],
        [analytics_button, schedule_button],
        [KeyboardButton(text="🔐 Веб-админка"), KeyboardButton(text="ℹ️ Помощь")],
    ]

    # Добавляем кнопку управления владельцами для супер-админа
    if is_super_admin:
        buttons.append([KeyboardButton(text="👥 Управление владельцами")])

    # Добавляем кнопки переключения режима
    mode_buttons = [KeyboardButton(text="👤 Режим клиента")]
    if show_employee_button:
        mode_buttons.append(KeyboardButton(text="💼 Режим сотрудника"))
    buttons.append(mode_buttons)

    keyboard = ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )
    return keyboard


def get_businesses_admin_keyboard(businesses: List[Business], callback_prefix: str = "admin_business") -> InlineKeyboardMarkup:
    """Клавиатура для управления бизнесами"""
    builder = InlineKeyboardBuilder()

    for business in businesses:
        status = "✅" if business.is_active else "⏸"
        builder.button(
            text=f"{status} {business.name}",
            callback_data=f"{callback_prefix}:{business.id}"
        )

    builder.button(text="➕ Создать бизнес", callback_data="create_business", style="primary")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_business_management_keyboard(business_id: int, business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура управления конкретным бизнесом

    Args:
        business_id: ID бизнеса
        business_type: тип бизнеса для определения терминологии
    """
    from bot.utils.business_types import is_venue_rental_type

    builder = InlineKeyboardBuilder()

    builder.button(text="✏️ Редактировать", callback_data=f"edit_business:{business_id}")

    # Используем разную терминологию для venue_rental
    if is_venue_rental_type(business_type):
        builder.button(text="⏱ Время аренды", callback_data=f"manage_services:{business_id}")
        builder.button(text="🏠 Помещения", callback_data=f"manage_employees:{business_id}")
        builder.button(text="🛎 Доп. услуги", callback_data=f"manage_additional_services:{business_id}")
    else:
        builder.button(text="📋 Услуги", callback_data=f"manage_services:{business_id}")
        builder.button(text="👥 Сотрудники", callback_data=f"manage_employees:{business_id}")

    builder.button(text="🎫 Абонементы", callback_data=f"manage_subscriptions:{business_id}")
    builder.button(text="👥 Совладельцы", callback_data=f"manage_owners:{business_id}")
    builder.button(text="📋 Дежурные сегодня", callback_data=f"duty_admins:{business_id}")
    builder.button(text="📅 График дежурств", callback_data=f"duty_schedule:{business_id}")
    builder.button(text="📲 QR-код (Telegram)", callback_data=f"show_qr:{business_id}")
    builder.button(text="🌐 QR-код (WebApp)", callback_data=f"show_webapp_qr:{business_id}")
    builder.button(text="🗑 Удалить бизнес", callback_data=f"delete_business:{business_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data="admin_back_to_list")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")

    builder.adjust(1)
    return builder.as_markup()


def get_business_edit_keyboard(business_id: int, show_prepayment: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура выбора поля для редактирования

    Args:
        business_id: ID бизнеса
        show_prepayment: показывать настройки предоплаты
    """
    builder = InlineKeyboardBuilder()

    builder.button(text="📝 Название", callback_data=f"edit_field:name:{business_id}")
    builder.button(text="📄 Описание", callback_data=f"edit_field:description:{business_id}")
    builder.button(text="📍 Адрес", callback_data=f"edit_field:address:{business_id}")
    builder.button(text="📞 Телефон", callback_data=f"edit_field:phone:{business_id}")
    builder.button(text="⏰ Время работы", callback_data=f"edit_field:hours:{business_id}")
    builder.button(text="☕ Перерыв", callback_data=f"edit_field:break:{business_id}")
    builder.button(text="📷 Фотографии", callback_data=f"edit_field:photos:{business_id}")
    builder.button(text="🎬 Видеообзор", callback_data=f"edit_field:video:{business_id}")
    builder.button(text="🗺 Как добраться", callback_data=f"edit_field:directions:{business_id}")
    if show_prepayment:
        builder.button(text="💳 Настройки предоплаты", callback_data=f"edit_prepayment:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")

    builder.adjust(1)
    return builder.as_markup()


def get_prepayment_settings_keyboard(
    business_id: int,
    prepayment_enabled: bool,
    require_receipt: bool = False,
    has_cancellation_photo: bool = False,
    has_cancellation_text: bool = False,
) -> InlineKeyboardMarkup:
    """Клавиатура настроек предоплаты"""
    builder = InlineKeyboardBuilder()

    if prepayment_enabled:
        builder.button(text="❌ Отключить предоплату", callback_data=f"prepay_toggle:{business_id}:off", style="danger")
        builder.button(text="💰 Изменить процент", callback_data=f"prepay_percent:{business_id}")
        builder.button(text="📱 Изменить номер для оплаты", callback_data=f"prepay_phone:{business_id}")
        builder.button(text="📝 Изменить реквизиты", callback_data=f"prepay_details:{business_id}")

        # Требовать чек
        if require_receipt:
            builder.button(text="✅ Чек: обязателен (нажмите чтобы отключить)", callback_data=f"prepay_require_receipt:{business_id}:off")
        else:
            builder.button(text="📸 Чек: не обязателен (нажмите чтобы включить)", callback_data=f"prepay_require_receipt:{business_id}:on")

        # Фото условий отмены
        if has_cancellation_photo:
            builder.button(text="🖼 Изменить фото условий отмены", callback_data=f"prepay_cancel_photo:{business_id}")
        else:
            builder.button(text="📷 Добавить фото условий отмены", callback_data=f"prepay_cancel_photo:{business_id}")

        # Текст условий отмены
        if has_cancellation_text:
            builder.button(text="📝 Изменить текст условий отмены", callback_data=f"prepay_cancel_text:{business_id}")
        else:
            builder.button(text="✏️ Добавить текст условий отмены", callback_data=f"prepay_cancel_text:{business_id}")
    else:
        builder.button(text="✅ Включить предоплату", callback_data=f"prepay_toggle:{business_id}:on", style="success")

    builder.button(text="◀️ Назад", callback_data=f"edit_business:{business_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_business_types_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура для выбора типа бизнеса"""
    builder = InlineKeyboardBuilder()

    types = [
        ("💇 Салон красоты", "salon"),
        ("🧠 Психолог", "psychologist"),
        ("🚗 Автосервис", "auto_service"),
        ("🔧 Шиномонтаж", "tire_service"),
        ("💆 Массаж/СПА", "spa"),
        ("🏥 Медицинская клиника", "medical"),
        ("🎓 Обучение", "education"),
        ("🏠 Аренда помещений", "venue_rental"),  # Сауны, студии и т.п.
        ("✂️ Другое", "other"),
    ]

    for text, callback in types:
        builder.button(text=text, callback_data=f"business_type:{callback}")

    builder.adjust(2)
    return builder.as_markup()


def get_bookings_filter_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура фильтров для просмотра записей"""
    builder = InlineKeyboardBuilder()

    builder.button(text="📅 Сегодня", callback_data="filter:today")
    builder.button(text="📅 Завтра", callback_data="filter:tomorrow")
    builder.button(text="📅 Неделя", callback_data="filter:week")
    builder.button(text="📅 Все", callback_data="filter:all")

    builder.adjust(2)
    return builder.as_markup()


def get_booking_admin_actions_keyboard(booking_id: int, show_prepayment_confirm: bool = False) -> InlineKeyboardMarkup:
    """Клавиатура действий администратора с записью

    Args:
        booking_id: ID записи
        show_prepayment_confirm: показывать кнопку подтверждения предоплаты
    """
    builder = InlineKeyboardBuilder()

    if show_prepayment_confirm:
        builder.button(text="✅ Подтвердить оплату", callback_data=f"confirm_prepayment:{booking_id}", style="success")
        builder.button(text="❌ Нет поступления / отклонить", callback_data=f"reject_prepayment:{booking_id}", style="danger")
    else:
        builder.button(text="✅ Подтвердить", callback_data=f"admin_confirm:{booking_id}", style="success")
        builder.button(text="✔️ Завершить", callback_data=f"admin_complete:{booking_id}", style="success")
        builder.button(text="❌ Отменить", callback_data=f"admin_cancel:{booking_id}", style="danger")

    builder.adjust(1)
    return builder.as_markup()


def get_photos_management_keyboard(business_id: int, has_photos: bool) -> InlineKeyboardMarkup:
    """Клавиатура управления фотографиями бизнеса"""
    builder = InlineKeyboardBuilder()

    builder.button(text="➕ Добавить фото", callback_data=f"add_photo:{business_id}")
    if has_photos:
        builder.button(text="🗑 Удалить все фото", callback_data=f"delete_all_photos:{business_id}", style="danger")
    builder.button(text="◀️ Назад к редактированию", callback_data=f"back_to_edit:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")

    builder.adjust(1)
    return builder.as_markup()


def get_admin_management_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура управления владельцами"""
    builder = InlineKeyboardBuilder()

    builder.button(text="➕ Добавить владельца", callback_data="add_admin")
    builder.button(text="🗑 Удалить владельца", callback_data="remove_admin", style="danger")
    builder.button(text="◀️ Назад", callback_data="back_to_admin_menu")

    builder.adjust(1)
    return builder.as_markup()


def get_admin_list_keyboard(admin_info: dict) -> InlineKeyboardMarkup:
    """Клавиатура со списком владельцев для удаления

    Args:
        admin_info: словарь {admin_id: display_name}
    """
    builder = InlineKeyboardBuilder()

    for admin_id, display_name in admin_info.items():
        builder.button(text=f"🗑 {display_name}", callback_data=f"delete_admin:{admin_id}")

    builder.button(text="◀️ Назад", callback_data="manage_admins")
    builder.adjust(1)
    return builder.as_markup()


def get_employee_services_keyboard(
    services: list,
    selected_service_ids: List[int],
    employee_id: int,
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора услуг, которые может делать сотрудник

    Args:
        services: список всех услуг бизнеса
        selected_service_ids: ID уже выбранных услуг
        employee_id: ID сотрудника
        business_id: ID бизнеса
    """
    builder = InlineKeyboardBuilder()

    for service in services:
        is_selected = service.id in selected_service_ids
        prefix = "✅" if is_selected else "◻️"
        builder.button(
            text=f"{prefix} {service.name}",
            callback_data=f"toggle_emp_svc:{employee_id}:{service.id}"
        )

    builder.button(text="💾 Сохранить", callback_data=f"save_emp_services:{employee_id}:{business_id}", style="success")
    builder.button(text="◀️ Назад", callback_data=f"manage_employees:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_business_owners_keyboard(business_id: int, owners_data: list) -> InlineKeyboardMarkup:
    """Клавиатура управления совладельцами бизнеса

    Args:
        business_id: ID бизнеса
        owners_data: список словарей с данными владельцев [{'user': User, 'role': str}, ...]
    """
    builder = InlineKeyboardBuilder()

    for owner_info in owners_data:
        user = owner_info['user']
        role = owner_info['role']
        role_emoji = "👑" if role == "owner" else "👤"
        display_name = user.username or user.full_name or f"ID: {user.telegram_id}"
        builder.button(
            text=f"{role_emoji} {display_name}",
            callback_data=f"owner_info:{business_id}:{user.id}"
        )

    builder.button(text="➕ Добавить совладельца", callback_data=f"add_owner:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")
    builder.button(text="🏠 Главное меню", callback_data="main_menu")

    builder.adjust(1)
    return builder.as_markup()


def get_owner_info_keyboard(business_id: int, user_id: int, can_remove: bool = True) -> InlineKeyboardMarkup:
    """Клавиатура информации о совладельце

    Args:
        business_id: ID бизнеса
        user_id: ID пользователя-владельца
        can_remove: можно ли удалить этого владельца
    """
    builder = InlineKeyboardBuilder()

    if can_remove:
        builder.button(text="🗑 Удалить совладельца", callback_data=f"remove_owner:{business_id}:{user_id}", style="danger")

    builder.button(text="◀️ Назад к списку", callback_data=f"manage_owners:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_confirm_remove_owner_keyboard(business_id: int, user_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения удаления совладельца"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Да, удалить", callback_data=f"confirm_remove_owner:{business_id}:{user_id}", style="danger")
    builder.button(text="❌ Отмена", callback_data=f"manage_owners:{business_id}")

    builder.adjust(2)
    return builder.as_markup()
