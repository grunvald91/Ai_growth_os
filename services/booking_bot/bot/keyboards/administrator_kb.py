"""
Клавиатуры для панели администратора (роль administrator в Employee)
"""
from typing import List
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Employee, Booking
from bot.config import WEBAPP_URL
from bot.utils.business_types import get_terminology, is_venue_rental_type


def get_administrator_menu_keyboard(show_owner_button: bool = False, business_type: str = None) -> ReplyKeyboardMarkup:
    """Главное меню для администратора бизнеса

    Args:
        show_owner_button: показывать кнопку "Режим владельца"
        business_type: тип бизнеса для терминологии
    """
    is_venue = is_venue_rental_type(business_type) if business_type else False
    term = get_terminology(business_type) if business_type else get_terminology("default")

    # Для venue_rental: "Выходные помещений" / "Услуги помещений" не имеет смысла
    # Используем общие термины
    if is_venue:
        days_off_text = "📅 Расписание"
        services_text = "🛠 Время аренды"
    else:
        days_off_text = f"📅 Выходные {term.get('employee_plural', 'мастеров')}"
        services_text = f"🛠 Услуги {term.get('employee_plural', 'мастеров')}"

    buttons = [
        [KeyboardButton(text="📋 Все записи")],
        [KeyboardButton(text="➕ Записать клиента"), KeyboardButton(text="❌ Отменить запись")],
        [KeyboardButton(text="👥 Клиенты"), KeyboardButton(text="📊 Статистика")],
        [KeyboardButton(text=days_off_text), KeyboardButton(text=services_text)],
    ]

    # Кнопка расписания (если настроен URL)
    if WEBAPP_URL:
        buttons.append([
            KeyboardButton(
                text="📅 Расписание",
                web_app=WebAppInfo(url=f"{WEBAPP_URL}/schedule?role=admin")
            )
        ])

    buttons.append([KeyboardButton(text="ℹ️ Помощь")])

    # Кнопки переключения режима
    mode_buttons = [KeyboardButton(text="👤 Режим клиента"), KeyboardButton(text="💼 Режим сотрудника")]
    if show_owner_button:
        mode_buttons.append(KeyboardButton(text="🏢 Режим владельца"))
    buttons.append(mode_buttons)

    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def get_admin_bookings_filter_keyboard() -> InlineKeyboardMarkup:
    """Фильтры для просмотра записей администратором"""
    builder = InlineKeyboardBuilder()

    builder.button(text="📅 Сегодня", callback_data="admin_filter:today")
    builder.button(text="📅 Завтра", callback_data="admin_filter:tomorrow")
    builder.button(text="📅 Неделя", callback_data="admin_filter:week")
    builder.button(text="📅 Месяц", callback_data="admin_filter:month")
    builder.button(text="📅 Все", callback_data="admin_filter:all")

    builder.adjust(2, 2, 1)
    return builder.as_markup()


def get_master_selection_keyboard(masters: List[Employee], business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора мастера/помещения для записи клиента"""
    builder = InlineKeyboardBuilder()

    for master in masters:
        spec_text = f" - {master.specialization}" if master.specialization else ""
        builder.button(
            text=f"{master.name}{spec_text}",
            callback_data=f"admin_master:{master.id}"
        )

    builder.button(text="❌ Отмена", callback_data="admin_cancel_action")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_booking_actions_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Действия администратора с записью"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Подтвердить", callback_data=f"admin_confirm_booking:{booking_id}", style="success")
    builder.button(text="✔️ Завершить", callback_data=f"admin_complete_booking:{booking_id}", style="success")
    builder.button(text="❌ Отменить", callback_data=f"admin_cancel_booking:{booking_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data="admin_back_to_bookings")

    builder.adjust(1)
    return builder.as_markup()


def get_bookings_list_keyboard(bookings: List[Booking]) -> InlineKeyboardMarkup:
    """Список записей для выбора (для отмены или просмотра)"""
    builder = InlineKeyboardBuilder()

    # Короткие названия дней недели
    weekdays_short = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    for booking in bookings[:20]:  # Лимит 20 записей
        status_emoji = {
            "pending": "⏳",
            "confirmed": "✅",
            "completed": "✔️",
            "cancelled": "❌"
        }.get(booking.status, "❓")

        time_str = booking.booking_time.strftime("%H:%M")
        weekday = weekdays_short[booking.booking_date.weekday()]
        date_str = f"{booking.booking_date.strftime('%d.%m')} {weekday}"
        client_name = booking.client.full_name[:12] if booking.client else "Без имени"

        builder.button(
            text=f"{status_emoji} {date_str} {time_str} - {client_name}",
            callback_data=f"admin_view_booking:{booking.id}"
        )

    builder.button(text="◀️ Назад", callback_data="admin_back_to_menu")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_repeat_toggle_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора: повторять запись каждую неделю или нет"""
    builder = InlineKeyboardBuilder()
    builder.button(text="Без повтора", callback_data="admin_repeat_skip")
    builder.button(text="🔁 Повторять →", callback_data="admin_repeat_enable")
    builder.adjust(1)
    return builder.as_markup()


def get_admin_repeat_stepper_keyboard(weeks: int) -> InlineKeyboardMarkup:
    """Клавиатура степпера числа недель повтора (2-8)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="−", callback_data="admin_repeat_dec")
    builder.button(text=f"{weeks} недель", callback_data="admin_repeat_noop")
    builder.button(text="+", callback_data="admin_repeat_inc")
    builder.button(text="✅ Готово", callback_data="admin_repeat_done")
    builder.button(text="❌ Отмена", callback_data="admin_repeat_cancel")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def get_admin_confirmation_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения действия"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Подтвердить", callback_data="admin_confirm_action", style="success")
    builder.button(text="❌ Отмена", callback_data="admin_cancel_action", style="danger")

    builder.adjust(2)
    return builder.as_markup()


def get_masters_for_days_off_keyboard(masters: List[Employee], business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора мастера/помещения для управления выходными"""
    builder = InlineKeyboardBuilder()
    is_venue = is_venue_rental_type(business_type) if business_type else False
    icon = "🏠" if is_venue else "👤"

    for master in masters:
        spec_text = f" ({master.specialization})" if master.specialization else ""
        builder.button(
            text=f"{icon} {master.name}{spec_text}",
            callback_data=f"daysoff_master:{master.id}"
        )

    builder.button(text="◀️ Закрыть", callback_data="daysoff_close")
    builder.adjust(1)
    return builder.as_markup()


def get_days_off_management_keyboard(employee_id: int, business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура управления выходными мастера/помещения"""
    builder = InlineKeyboardBuilder()
    is_venue = is_venue_rental_type(business_type) if business_type else False
    back_text = "◀️ К списку помещений" if is_venue else "◀️ К списку мастеров"

    builder.button(text="➕ Добавить выходной", callback_data=f"daysoff_add:{employee_id}")
    builder.button(text="📋 Список выходных", callback_data=f"daysoff_list:{employee_id}")
    builder.button(text="🗑 Удалить выходной", callback_data=f"daysoff_remove:{employee_id}", style="danger")
    builder.button(text=back_text, callback_data="daysoff_back_to_masters")

    builder.adjust(1)
    return builder.as_markup()


def get_masters_for_services_keyboard(masters: List[Employee], business_type: str = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора мастера/помещения для управления услугами"""
    builder = InlineKeyboardBuilder()
    is_venue = is_venue_rental_type(business_type) if business_type else False
    icon = "🏠" if is_venue else "👤"

    for master in masters:
        spec_text = f" ({master.specialization})" if master.specialization else ""
        builder.button(
            text=f"{icon} {master.name}{spec_text}",
            callback_data=f"emp_services_master:{master.id}"
        )

    builder.button(text="◀️ Закрыть", callback_data="emp_services_close")
    builder.adjust(1)
    return builder.as_markup()


def get_employee_services_select_keyboard(
    services: list,
    selected_service_ids: list,
    employee_id: int,
    business_type: str = None
) -> InlineKeyboardMarkup:
    """Клавиатура для выбора услуг мастера/помещения (с чекбоксами)"""
    builder = InlineKeyboardBuilder()
    is_venue = is_venue_rental_type(business_type) if business_type else False
    back_text = "◀️ К списку помещений" if is_venue else "◀️ К списку мастеров"

    for service in services:
        is_selected = service.id in selected_service_ids
        prefix = "✅" if is_selected else "◻️"
        builder.button(
            text=f"{prefix} {service.name}",
            callback_data=f"emp_svc_toggle:{employee_id}:{service.id}"
        )

    builder.button(text="💾 Сохранить", callback_data=f"emp_svc_save:{employee_id}", style="success")
    builder.button(text=back_text, callback_data="emp_services_back_to_masters")
    builder.adjust(1)
    return builder.as_markup()


def get_days_off_calendar_keyboard(
    employee_id: int,
    year: int,
    month: int,
    days_off: List[int],
    mode: str = "add"
) -> InlineKeyboardMarkup:
    """Календарь для выбора дня (добавление/удаление выходного)

    Args:
        employee_id: ID сотрудника
        year: год
        month: месяц
        days_off: список дней (числа), которые уже являются выходными
        mode: 'add' для добавления, 'remove' для удаления
    """
    import calendar
    from datetime import date

    builder = InlineKeyboardBuilder()

    # Названия месяцев
    month_names = [
        "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
        "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
    ]

    # Заголовок с месяцем и годом
    builder.button(text=f"📅 {month_names[month]} {year}", callback_data="noop")

    # Навигация по месяцам
    prev_month = month - 1
    prev_year = year
    if prev_month < 1:
        prev_month = 12
        prev_year -= 1

    next_month = month + 1
    next_year = year
    if next_month > 12:
        next_month = 1
        next_year += 1

    builder.button(text="◀️", callback_data=f"daysoff_cal:{employee_id}:{prev_year}:{prev_month}:{mode}")
    builder.button(text="▶️", callback_data=f"daysoff_cal:{employee_id}:{next_year}:{next_month}:{mode}")

    # Дни недели
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    for day in weekdays:
        builder.button(text=day, callback_data="noop")

    # Дни месяца
    cal = calendar.monthcalendar(year, month)
    today = date.today()

    for week in cal:
        for day in week:
            if day == 0:
                builder.button(text=" ", callback_data="noop")
            else:
                current_date = date(year, month, day)
                is_past = current_date < today
                is_day_off = day in days_off

                if mode == "add":
                    # В режиме добавления: выходные помечаем, прошедшие отключаем
                    if is_day_off:
                        builder.button(text=f"🔴{day}", callback_data="noop")
                    elif is_past:
                        builder.button(text=f"{day}", callback_data="noop")
                    else:
                        builder.button(text=f"{day}", callback_data=f"daysoff_set:{employee_id}:{year}:{month}:{day}")
                else:
                    # В режиме удаления: только выходные кликабельны
                    if is_day_off:
                        builder.button(text=f"🔴{day}", callback_data=f"daysoff_del:{employee_id}:{year}:{month}:{day}")
                    else:
                        builder.button(text=f"{day}", callback_data="noop")

    builder.button(text="◀️ Назад", callback_data=f"daysoff_master:{employee_id}")

    # Настройка раскладки: 1 + 2 + 7 дней недели + 7*n дней + 1 кнопка назад
    adjust = [1, 2, 7]  # заголовок + навигация + дни недели
    for week in cal:
        adjust.append(7)
    adjust.append(1)  # кнопка назад

    builder.adjust(*adjust)
    return builder.as_markup()
