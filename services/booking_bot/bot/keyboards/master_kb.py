"""
Клавиатуры для панели мастеров
"""
from typing import List
from aiogram.types import InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Service, Booking
from datetime import datetime


def get_master_menu_keyboard(show_owner_button: bool = False) -> ReplyKeyboardMarkup:
    """Главное меню для мастера

    Args:
        show_owner_button: показывать кнопку "Режим владельца"
    """
    buttons = [
        [KeyboardButton(text="📅 Моё расписание")],
        [KeyboardButton(text="➕ Записать клиента"), KeyboardButton(text="📊 Моя статистика")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ]

    # Добавляем кнопки переключения режима
    mode_buttons = [KeyboardButton(text="👤 Режим клиента"), KeyboardButton(text="💼 Режим сотрудника")]
    if show_owner_button:
        mode_buttons.append(KeyboardButton(text="🏢 Режим владельца"))
    buttons.append(mode_buttons)

    keyboard = ReplyKeyboardMarkup(
        keyboard=buttons,
        resize_keyboard=True
    )
    return keyboard


def get_services_for_booking_keyboard(services: List[Service], selected_ids: List[int] = None) -> InlineKeyboardMarkup:
    """Клавиатура выбора услуг для ручной записи (мультивыбор)"""
    builder = InlineKeyboardBuilder()
    selected_ids = selected_ids or []

    for service in services:
        price_text = f" - {service.price}₽" if service.price else ""
        # Отмечаем выбранные услуги галочкой
        check = "✅ " if service.id in selected_ids else ""
        builder.button(
            text=f"{check}{service.name} ({service.duration_minutes} мин{price_text})",
            callback_data=f"master_service:{service.id}"
        )

    # Кнопка "Готово" появляется только если выбрана хотя бы одна услуга
    if selected_ids:
        builder.button(text="✔️ Готово - продолжить", callback_data="master_services_done", style="success")

    builder.button(text="❌ Отменить запись", callback_data="cancel_master_booking_flow", style="danger")
    builder.adjust(1)
    return builder.as_markup()


def get_master_bookings_keyboard(bookings: List[Booking]) -> InlineKeyboardMarkup:
    """Клавиатура с записями мастера на сегодня"""
    builder = InlineKeyboardBuilder()

    for booking in bookings:
        time_str = booking.booking_time.strftime('%H:%M')
        status_emoji = "✅" if booking.status == "confirmed" else "⏳"
        builder.button(
            text=f"{status_emoji} {time_str} - {booking.client.full_name}",
            callback_data=f"master_booking:{booking.id}"
        )

    if not bookings:
        builder.button(text="Нет записей на сегодня", callback_data="none")

    builder.adjust(1)
    return builder.as_markup()


def get_master_repeat_toggle_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура выбора: повторять запись каждую неделю или нет"""
    builder = InlineKeyboardBuilder()
    builder.button(text="Без повтора", callback_data="master_repeat_skip")
    builder.button(text="🔁 Повторять →", callback_data="master_repeat_enable")
    builder.adjust(1)
    return builder.as_markup()


def get_master_repeat_stepper_keyboard(weeks: int) -> InlineKeyboardMarkup:
    """Клавиатура степпера числа недель повтора (2-8)"""
    builder = InlineKeyboardBuilder()
    builder.button(text="−", callback_data="master_repeat_dec")
    builder.button(text=f"{weeks} недель", callback_data="master_repeat_noop")
    builder.button(text="+", callback_data="master_repeat_inc")
    builder.button(text="✅ Готово", callback_data="master_repeat_done")
    builder.button(text="❌ Отмена", callback_data="master_repeat_cancel")
    builder.adjust(3, 1, 1)
    return builder.as_markup()


def get_confirmation_keyboard_master() -> InlineKeyboardMarkup:
    """Клавиатура подтверждения ручной записи"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Подтвердить", callback_data="confirm_manual_booking", style="success")
    builder.button(text="◀️ Назад", callback_data="back_to_master_time_selection")
    builder.button(text="❌ Отменить", callback_data="cancel_manual_booking", style="danger")

    builder.adjust(1)
    return builder.as_markup()


def get_schedule_filter_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура фильтров расписания"""
    builder = InlineKeyboardBuilder()

    builder.button(text="📅 Сегодня", callback_data="master_schedule:today")
    builder.button(text="📅 Завтра", callback_data="master_schedule:tomorrow")
    builder.button(text="📅 Неделя", callback_data="master_schedule:week")

    builder.adjust(3)
    return builder.as_markup()


def get_master_booking_actions_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    """Клавиатура действий мастера с записью"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✔️ Завершить (освободить время)", callback_data=f"master_complete:{booking_id}", style="success")
    builder.button(text="🔁 Сменить услугу", callback_data=f"master_chsvc:{booking_id}")
    builder.button(text="❌ Отменить", callback_data=f"master_cancel:{booking_id}", style="danger")
    builder.button(text="👤 История клиента", callback_data=f"master_client_history:{booking_id}")
    builder.button(text="◀️ Назад", callback_data="master_schedule:today")

    builder.adjust(1)
    return builder.as_markup()
