"""
Клавиатуры для абонементов
"""

from datetime_utils import utc_now
from typing import List, Dict, Optional
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import Subscription, ClientSubscription, Service

# Количество элементов на странице
ITEMS_PER_PAGE = 10


def _add_pagination_buttons(
    builder: InlineKeyboardBuilder,
    current_page: int,
    total_items: int,
    callback_prefix: str
):
    """Добавляет кнопки пагинации к клавиатуре"""
    total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE

    if total_pages <= 1:
        return

    nav_buttons = []

    if current_page > 0:
        nav_buttons.append(("◀️", f"{callback_prefix}:page:{current_page - 1}"))

    nav_buttons.append((f"{current_page + 1}/{total_pages}", "noop"))

    if current_page < total_pages - 1:
        nav_buttons.append(("▶️", f"{callback_prefix}:page:{current_page + 1}"))

    for text, callback_data in nav_buttons:
        builder.button(text=text, callback_data=callback_data)

    # Размещаем кнопки навигации в одну строку
    builder.adjust(len(nav_buttons))


# ===== Клавиатуры для владельца =====

def get_subscriptions_management_keyboard(business_id: int) -> InlineKeyboardMarkup:
    """Клавиатура управления абонементами бизнеса"""
    builder = InlineKeyboardBuilder()

    builder.button(text="➕ Создать абонемент", callback_data=f"create_subscription:{business_id}", style="primary")
    builder.button(text="📋 Список абонементов", callback_data=f"list_subscriptions:{business_id}")
    builder.button(text="📊 Проданные абонементы", callback_data=f"sold_subscriptions:{business_id}")
    builder.button(text="⏳ Ожидают подтверждения", callback_data=f"pending_sub_payments:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"admin_business:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_subscriptions_list_keyboard(
    subscriptions: List[Subscription],
    business_id: int,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура со списком абонементов"""
    builder = InlineKeyboardBuilder()

    total_items = len(subscriptions)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = subscriptions[start_idx:end_idx]

    for sub in page_items:
        status = "✅" if sub.is_active else "⏸"
        builder.button(
            text=f"{status} {sub.name} - {sub.price}₽",
            callback_data=f"view_subscription:{sub.id}"
        )

    # Кнопки на странице
    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"list_subscriptions:{business_id}:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"list_subscriptions:{business_id}:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="➕ Создать абонемент", callback_data=f"create_subscription:{business_id}", style="primary")
    builder.button(text="◀️ Назад", callback_data=f"manage_subscriptions:{business_id}")

    return builder.as_markup()


def get_subscription_actions_keyboard(
    subscription_id: int,
    business_id: int,
    is_active: bool
) -> InlineKeyboardMarkup:
    """Клавиатура действий с абонементом"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✏️ Редактировать", callback_data=f"edit_subscription:{subscription_id}")

    if is_active:
        builder.button(text="⏸ Деактивировать", callback_data=f"deactivate_subscription:{subscription_id}", style="danger")
    else:
        builder.button(text="✅ Активировать", callback_data=f"activate_subscription:{subscription_id}")

    builder.button(text="🗑 Удалить", callback_data=f"delete_subscription:{subscription_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"list_subscriptions:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_services_selection_keyboard(
    services: List[Service],
    selected_ids: List[int],
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура мультивыбора услуг для абонемента"""
    builder = InlineKeyboardBuilder()

    for service in services:
        is_selected = service.id in selected_ids
        prefix = "✅" if is_selected else "◻️"
        price_text = f" - {service.price}₽" if service.price else ""
        builder.button(
            text=f"{prefix} {service.name}{price_text}",
            callback_data=f"sub_toggle_svc:{service.id}"
        )

    if selected_ids:
        builder.button(text="✔️ Готово - продолжить", callback_data="sub_services_done", style="success")

    builder.button(text="❌ Отмена", callback_data=f"manage_subscriptions:{business_id}", style="danger")

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_quantity_keyboard(
    service_name: str,
    current_quantity: int,
    service_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура +/- для выбора количества услуги"""
    builder = InlineKeyboardBuilder()

    builder.button(text="➖", callback_data=f"sub_qty_dec:{service_id}")
    builder.button(text=str(current_quantity), callback_data="noop")
    builder.button(text="➕", callback_data=f"sub_qty_inc:{service_id}")
    builder.button(text="✅ Сохранить", callback_data=f"sub_qty_save:{service_id}", style="success")

    builder.adjust(3, 1)
    return builder.as_markup()


def get_subscription_edit_keyboard(subscription_id: int, business_id: int) -> InlineKeyboardMarkup:
    """Клавиатура выбора поля для редактирования абонемента"""
    builder = InlineKeyboardBuilder()

    builder.button(text="📝 Название", callback_data=f"edit_sub_field:name:{subscription_id}")
    builder.button(text="📄 Описание", callback_data=f"edit_sub_field:description:{subscription_id}")
    builder.button(text="💰 Цена", callback_data=f"edit_sub_field:price:{subscription_id}")
    builder.button(text="📅 Срок действия", callback_data=f"edit_sub_field:validity:{subscription_id}")
    builder.button(text="📋 Правила", callback_data=f"edit_sub_field:rules:{subscription_id}")
    builder.button(text="🔧 Услуги", callback_data=f"edit_sub_field:services:{subscription_id}")
    builder.button(text="◀️ Назад", callback_data=f"view_subscription:{subscription_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_sold_subscriptions_keyboard(
    client_subscriptions: List[ClientSubscription],
    business_id: int,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура проданных абонементов"""
    builder = InlineKeyboardBuilder()

    total_items = len(client_subscriptions)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = client_subscriptions[start_idx:end_idx]

    for cs in page_items:
        status_emoji = {
            "pending_payment": "⏳",
            "payment_sent": "💳",
            "active": "✅",
            "expired": "⌛",
            "depleted": "📭"
        }.get(cs.status, "❓")

        client_name = cs.client.full_name if cs.client else "?"
        builder.button(
            text=f"{status_emoji} {cs.subscription.name} - {client_name}",
            callback_data=f"view_sold_sub:{cs.id}"
        )

    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"sold_subscriptions:{business_id}:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"sold_subscriptions:{business_id}:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="◀️ Назад", callback_data=f"manage_subscriptions:{business_id}")

    return builder.as_markup()


def get_pending_payments_list_keyboard(
    pending_subscriptions: List[ClientSubscription],
    business_id: int,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура списка абонементов, ожидающих подтверждения оплаты"""
    builder = InlineKeyboardBuilder()

    total_items = len(pending_subscriptions)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = pending_subscriptions[start_idx:end_idx]

    for cs in page_items:
        client_name = cs.client.full_name if cs.client else "?"
        builder.button(
            text=f"💳 {cs.subscription.name} - {client_name}",
            callback_data=f"view_pending_sub:{cs.id}"
        )

    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"pending_sub_payments:{business_id}:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"pending_sub_payments:{business_id}:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="◀️ Назад", callback_data=f"manage_subscriptions:{business_id}")

    return builder.as_markup()


def get_pending_payment_keyboard(
    client_subscription_id: int,
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура для подтверждения/отклонения оплаты абонемента"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Подтвердить оплату", callback_data=f"confirm_sub_payment:{client_subscription_id}", style="success")
    builder.button(text="❌ Отклонить (нет оплаты)", callback_data=f"reject_sub_payment:{client_subscription_id}", style="danger")
    builder.button(text="◀️ Назад", callback_data=f"pending_sub_payments:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


# ===== Клавиатуры для клиента =====

def get_available_subscriptions_keyboard(
    subscriptions: List[Subscription],
    business_id: int,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура доступных абонементов для покупки"""
    builder = InlineKeyboardBuilder()

    total_items = len(subscriptions)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = subscriptions[start_idx:end_idx]

    for sub in page_items:
        # Убираем слово "Абонемент" из названия — и так понятно
        display_name = sub.name
        for prefix in ("Абонемент ", "абонемент "):
            if display_name.startswith(prefix):
                display_name = display_name[len(prefix):]
                break
        builder.button(
            text=f"🎫 {display_name} - {sub.price}₽",
            callback_data=f"sub_details:{sub.id}"
        )

    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"view_subscriptions:{business_id}:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"view_subscriptions:{business_id}:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="◀️ Назад", callback_data="back_to_services")

    return builder.as_markup()


def get_subscription_details_keyboard(
    subscription_id: int,
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура деталей абонемента для клиента"""
    builder = InlineKeyboardBuilder()

    builder.button(text="💳 Купить", callback_data=f"buy_subscription:{subscription_id}", style="primary")
    builder.button(text="◀️ Назад", callback_data=f"view_subscriptions:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_purchase_keyboard(
    client_subscription_id: int,
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура после создания заказа абонемента (показ реквизитов)"""
    builder = InlineKeyboardBuilder()

    builder.button(text="📸 Прикрепить чек", callback_data=f"sub_attach_receipt:{client_subscription_id}")
    builder.button(text="✅ Оплата отправлена", callback_data=f"sub_payment_sent:{client_subscription_id}", style="success")
    builder.button(text="❌ Отменить покупку", callback_data=f"cancel_sub_purchase:{client_subscription_id}", style="danger")

    builder.adjust(1)
    return builder.as_markup()


def get_my_subscriptions_keyboard(
    client_subscriptions: List[ClientSubscription],
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура списка абонементов клиента"""
    builder = InlineKeyboardBuilder()

    total_items = len(client_subscriptions)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = client_subscriptions[start_idx:end_idx]

    for cs in page_items:
        status_emoji = {
            "pending_payment": "⏳",
            "payment_sent": "💳",
            "active": "✅",
            "expired": "⌛",
            "depleted": "📭"
        }.get(cs.status, "❓")

        expires_text = ""
        if cs.status == "active" and cs.expires_at:
            days_left = (cs.expires_at - utc_now()).days
            if days_left > 0:
                expires_text = f" ({days_left} дн.)"
            else:
                expires_text = " (истекает)"

        builder.button(
            text=f"{status_emoji} {cs.subscription.name}{expires_text}",
            callback_data=f"my_sub_details:{cs.id}"
        )

    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"my_subscriptions:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"my_subscriptions:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="🏠 Главное меню", callback_data="client_main_menu")

    return builder.as_markup()


def get_my_subscription_details_keyboard(
    client_subscription_id: int,
    status: str
) -> InlineKeyboardMarkup:
    """Клавиатура деталей купленного абонемента"""
    builder = InlineKeyboardBuilder()

    if status in ["pending_payment", "payment_sent"]:
        builder.button(text="📸 Прикрепить чек", callback_data=f"sub_attach_receipt:{client_subscription_id}")
        if status == "pending_payment":
            builder.button(text="✅ Оплата отправлена", callback_data=f"sub_payment_sent:{client_subscription_id}", style="success")
        builder.button(text="❌ Отменить покупку", callback_data=f"cancel_sub_purchase:{client_subscription_id}", style="danger")

    builder.button(text="◀️ К моим абонементам", callback_data="my_subscriptions")

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_services_for_booking_keyboard(
    available_services: List[Dict],
    business_id: int,
    page: int = 0
) -> InlineKeyboardMarkup:
    """Клавиатура услуг из абонемента для использования при бронировании

    Args:
        available_services: список словарей с информацией о доступных услугах
            [{"client_subscription_id", "subscription_name", "service_id", "service_name", "remaining"}, ...]
        business_id: ID бизнеса
        page: номер страницы
    """
    builder = InlineKeyboardBuilder()

    total_items = len(available_services)
    start_idx = page * ITEMS_PER_PAGE
    end_idx = start_idx + ITEMS_PER_PAGE
    page_items = available_services[start_idx:end_idx]

    for svc in page_items:
        builder.button(
            text=f"🎫 {svc['service_name']} ({svc['remaining']} шт.) - {svc['subscription_name']}",
            callback_data=f"use_sub_svc:{svc['client_subscription_id']}:{svc['service_id']}"
        )

    buttons_count = len(page_items)
    builder.adjust(1)

    # Пагинация
    if total_items > ITEMS_PER_PAGE:
        total_pages = (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE
        nav_row = []
        if page > 0:
            nav_row.append(("◀️", f"use_subscription:{business_id}:page:{page - 1}"))
        nav_row.append((f"{page + 1}/{total_pages}", "noop"))
        if page < total_pages - 1:
            nav_row.append(("▶️", f"use_subscription:{business_id}:page:{page + 1}"))

        for text, cb in nav_row:
            builder.button(text=text, callback_data=cb)
        builder.adjust(*([1] * buttons_count + [len(nav_row)]))

    builder.button(text="💰 Обычная запись", callback_data=f"regular_booking:{business_id}")
    builder.button(text="◀️ Назад", callback_data=f"business:{business_id}")

    return builder.as_markup()


def get_subscription_confirm_use_keyboard(
    client_subscription_id: int,
    service_id: int,
    business_id: int
) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения использования услуги из абонемента"""
    builder = InlineKeyboardBuilder()

    builder.button(
        text="✅ Использовать из абонемента",
        callback_data=f"confirm_sub_use:{client_subscription_id}:{service_id}",
        style="success"
    )
    builder.button(text="◀️ Назад", callback_data=f"use_subscription:{business_id}")

    builder.adjust(1)
    return builder.as_markup()


def get_subscription_creation_confirm_keyboard(business_id: int) -> InlineKeyboardMarkup:
    """Клавиатура подтверждения создания абонемента"""
    builder = InlineKeyboardBuilder()

    builder.button(text="✅ Создать абонемент", callback_data="confirm_create_subscription", style="success")
    builder.button(text="✏️ Редактировать", callback_data="edit_new_subscription")
    builder.button(text="❌ Отмена", callback_data=f"manage_subscriptions:{business_id}", style="danger")

    builder.adjust(1)
    return builder.as_markup()


def get_skip_button_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    """Клавиатура с кнопкой 'Пропустить'"""
    builder = InlineKeyboardBuilder()
    builder.button(text="⏭ Пропустить", callback_data=callback_data)
    builder.adjust(1)
    return builder.as_markup()
