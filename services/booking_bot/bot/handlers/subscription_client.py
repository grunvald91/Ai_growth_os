"""
Обработчики для работы с абонементами (клиент)
"""

from datetime_utils import utc_now
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database import async_session_maker
from database.crud import (
    get_or_create_user,
    get_business_by_id,
    get_business_subscriptions,
    get_subscription_by_id,
    purchase_subscription,
    get_client_subscriptions,
    get_client_subscription_by_id,
    set_payment_receipt,
    update_client_subscription_status,
    get_subscription_remaining,
    get_client_available_services,
    get_business_employees,
    get_service_by_id,
)
from bot.states.subscription import SubscriptionPurchaseStates
from bot.states import BookingStates
from bot.keyboards import subscription_kb, client_kb
from bot.utils.business_types import get_terminology

router = Router()


# ===== ПРОСМОТР ДОСТУПНЫХ АБОНЕМЕНТОВ =====

@router.callback_query(F.data.startswith("view_subscriptions:"))
async def view_business_subscriptions(callback: CallbackQuery, state: FSMContext):
    """Просмотр абонементов бизнеса для покупки"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    page = int(parts[3]) if len(parts) > 3 and parts[2] == "page" else 0

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

        subscriptions = await get_business_subscriptions(session, business_id, active_only=True)

    if not subscriptions:
        await callback.message.edit_text(
            f"У {business.name} пока нет доступных абонементов",
            reply_markup=client_kb.get_businesses_keyboard([business])
        )
        await callback.answer()
        return

    term = get_terminology(business.business_type)

    await callback.message.edit_text(
        f"🎫 <b>{business.name}</b>\n\n"
        f"Выберите для просмотра деталей:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_available_subscriptions_keyboard(subscriptions, business_id, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("sub_details:"))
async def view_subscription_details(callback: CallbackQuery, state: FSMContext):
    """Просмотр деталей абонемента"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        subscription = await get_subscription_by_id(session, subscription_id)
        if not subscription:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

    # Формируем список услуг
    services_text = ""
    total_value = 0
    for sub_svc in subscription.services:
        service = sub_svc.service
        services_text += f"  • {service.name} x{sub_svc.quantity}"
        if service.price:
            service_total = service.price * sub_svc.quantity
            total_value += service_total
            services_text += f" ({service_total}₽)"
        services_text += "\n"

    # Расчет экономии
    savings_text = ""
    if total_value > subscription.price:
        savings = total_value - subscription.price
        savings_percent = int((savings / total_value) * 100)
        savings_text = f"💰 Экономия: {savings}₽ ({savings_percent}%)\n\n"

    text = (
        f"🎫 <b>{subscription.name}</b>\n\n"
        f"💵 Цена: <b>{subscription.price}₽</b>\n"
        f"📅 Срок действия: {subscription.validity_days} дней\n"
    )

    if total_value > 0:
        text += f"📊 Стоимость услуг по отдельности: {total_value}₽\n"

    text += f"\n{savings_text}"

    if subscription.description:
        text += f"📝 Описание:\n{subscription.description}\n\n"

    text += f"📋 В абонемент входит:\n{services_text}\n"

    if subscription.rules:
        text += f"📖 Правила:\n{subscription.rules}\n"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_details_keyboard(
            subscription_id,
            subscription.business_id
        )
    )
    await callback.answer()


# ===== ПОКУПКА АБОНЕМЕНТА =====

@router.callback_query(F.data.startswith("buy_subscription:"))
async def buy_subscription(callback: CallbackQuery, state: FSMContext):
    """Начало покупки абонемента"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        subscription = await get_subscription_by_id(session, subscription_id)
        if not subscription:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        business = subscription.business

        # Проверяем, есть ли реквизиты для оплаты
        if not business.payment_phone and not business.payment_details:
            await callback.answer(
                "Владелец не настроил реквизиты для оплаты. Свяжитесь с ним напрямую.",
                show_alert=True
            )
            return

        # Получаем пользователя
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        # Создаем запись о покупке
        client_sub = await purchase_subscription(
            session,
            subscription_id=subscription_id,
            client_id=user.id,
            business_id=business.id
        )

    # Формируем сообщение с реквизитами
    payment_text = (
        f"💳 <b>Оплата абонемента</b>\n\n"
        f"🎫 Абонемент: {subscription.name}\n"
        f"💰 Сумма к оплате: <b>{subscription.price}₽</b>\n\n"
        f"📝 <b>Реквизиты для оплаты:</b>\n"
    )

    if business.payment_phone:
        from bot.utils.validators import format_phone
        payment_text += f"📱 Телефон: {format_phone(business.payment_phone)}\n"

    if business.payment_details:
        payment_text += f"{business.payment_details}\n"

    payment_text += (
        f"\n⚠️ <b>Важно:</b> После перевода денег нажмите кнопку "
        f"'Оплата отправлена' или прикрепите чек для подтверждения.\n\n"
        f"После подтверждения владельцем абонемент будет активирован."
    )

    await callback.message.edit_text(
        payment_text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_purchase_keyboard(
            client_sub.id,
            business.id
        )
    )
    await state.set_state(SubscriptionPurchaseStates.waiting_payment)
    await state.update_data(client_subscription_id=client_sub.id)
    await callback.answer()


@router.callback_query(F.data.startswith("sub_payment_sent:"))
async def subscription_payment_sent(callback: CallbackQuery, state: FSMContext):
    """Клиент подтвердил отправку оплаты"""
    import logging
    logger = logging.getLogger(__name__)

    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if not cs:
            await callback.answer("Заявка не найдена", show_alert=True)
            return

        # Обновляем статус
        await update_client_subscription_status(session, client_sub_id, "payment_sent")

        business = cs.business
        subscription_name = cs.subscription.name
        subscription_price = cs.subscription.price
        client_name = callback.from_user.full_name
        owner_telegram_id = business.owner.telegram_id if business.owner else None
        business_id = business.id

    # Уведомляем владельца
    if owner_telegram_id:
        try:
            await callback.bot.send_message(
                owner_telegram_id,
                f"💳 <b>Новая заявка на абонемент!</b>\n\n"
                f"🎫 Абонемент: {subscription_name}\n"
                f"👤 Клиент: {client_name}\n"
                f"💰 Сумма: {subscription_price}₽\n\n"
                f"Клиент сообщил, что отправил оплату.\n"
                f"Проверьте поступление и подтвердите:",
                parse_mode="HTML",
                reply_markup=subscription_kb.get_pending_payment_keyboard(client_sub_id, business_id)
            )
            logger.info(f"Sent payment notification to owner {owner_telegram_id} for subscription {client_sub_id}")
        except Exception as e:
            logger.error(f"Failed to send payment notification to owner {owner_telegram_id}: {e}")
    else:
        logger.warning(f"No owner found for business {business_id}")

    await state.clear()

    await callback.message.edit_text(
        f"✅ <b>Заявка отправлена!</b>\n\n"
        f"🎫 Абонемент: {subscription_name}\n\n"
        f"Ожидайте подтверждения оплаты от владельца.\n"
        f"Вы получите уведомление, когда абонемент будет активирован.",
        parse_mode="HTML"
    )
    await callback.answer("Заявка отправлена!")


@router.callback_query(F.data.startswith("sub_attach_receipt:"))
async def start_attach_receipt(callback: CallbackQuery, state: FSMContext):
    """Начало прикрепления чека"""
    client_sub_id = int(callback.data.split(":")[1])

    await state.update_data(client_subscription_id=client_sub_id)
    await state.set_state(SubscriptionPurchaseStates.uploading_receipt)

    await callback.message.edit_text(
        "📸 <b>Прикрепление чека</b>\n\n"
        "Отправьте фото или документ с чеком об оплате:",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(SubscriptionPurchaseStates.uploading_receipt, F.photo)
async def receipt_photo_uploaded(message: Message, state: FSMContext):
    """Получение фото чека"""
    import logging
    logger = logging.getLogger(__name__)

    data = await state.get_data()
    client_sub_id = data.get("client_subscription_id")

    if not client_sub_id:
        await message.answer("Ошибка: заявка не найдена")
        await state.clear()
        return

    # Получаем file_id фото (берем самый большой размер)
    photo = message.photo[-1]
    file_id = photo.file_id

    async with async_session_maker() as session:
        cs = await set_payment_receipt(session, client_sub_id, file_id)
        if not cs:
            await message.answer("Ошибка: заявка не найдена")
            await state.clear()
            return

        business = cs.business
        subscription_name = cs.subscription.name
        subscription_price = cs.subscription.price
        client_name = message.from_user.full_name
        owner_telegram_id = business.owner.telegram_id if business.owner else None
        business_id = business.id

    # Уведомляем владельца
    if owner_telegram_id:
        try:
            await message.bot.send_message(
                owner_telegram_id,
                f"💳 <b>Новая заявка на абонемент!</b>\n\n"
                f"🎫 Абонемент: {subscription_name}\n"
                f"👤 Клиент: {client_name}\n"
                f"💰 Сумма: {subscription_price}₽\n"
                f"📎 Чек прикреплен (см. ниже)\n\n"
                f"Проверьте и подтвердите:",
                parse_mode="HTML",
                reply_markup=subscription_kb.get_pending_payment_keyboard(client_sub_id, business_id)
            )
            # Пересылаем чек владельцу
            await message.bot.send_photo(
                owner_telegram_id,
                file_id,
                caption=f"📎 Чек от {client_name}"
            )
            logger.info(f"Sent receipt notification to owner {owner_telegram_id} for subscription {client_sub_id}")
        except Exception as e:
            logger.error(f"Failed to send receipt notification to owner {owner_telegram_id}: {e}")
    else:
        logger.warning(f"No owner found for business {business_id}")

    await state.clear()

    await message.answer(
        f"✅ <b>Чек прикреплен, заявка отправлена!</b>\n\n"
        f"🎫 Абонемент: {subscription_name}\n\n"
        f"Ожидайте подтверждения оплаты от владельца.\n"
        f"Вы получите уведомление, когда абонемент будет активирован.",
        parse_mode="HTML"
    )


@router.message(SubscriptionPurchaseStates.uploading_receipt, F.document)
async def receipt_document_uploaded(message: Message, state: FSMContext):
    """Получение документа-чека"""
    import logging
    logger = logging.getLogger(__name__)

    data = await state.get_data()
    client_sub_id = data.get("client_subscription_id")

    if not client_sub_id:
        await message.answer("Ошибка: заявка не найдена")
        await state.clear()
        return

    file_id = message.document.file_id

    async with async_session_maker() as session:
        cs = await set_payment_receipt(session, client_sub_id, file_id)
        if not cs:
            await message.answer("Ошибка: заявка не найдена")
            await state.clear()
            return

        business = cs.business
        subscription_name = cs.subscription.name
        subscription_price = cs.subscription.price
        client_name = message.from_user.full_name
        owner_telegram_id = business.owner.telegram_id if business.owner else None
        business_id = business.id

    # Уведомляем владельца
    if owner_telegram_id:
        try:
            await message.bot.send_message(
                owner_telegram_id,
                f"💳 <b>Новая заявка на абонемент!</b>\n\n"
                f"🎫 Абонемент: {subscription_name}\n"
                f"👤 Клиент: {client_name}\n"
                f"💰 Сумма: {subscription_price}₽\n"
                f"📎 Чек прикреплен (см. ниже)\n\n"
                f"Проверьте и подтвердите:",
                parse_mode="HTML",
                reply_markup=subscription_kb.get_pending_payment_keyboard(client_sub_id, business_id)
            )
            await message.bot.send_document(
                owner_telegram_id,
                file_id,
                caption=f"📎 Чек от {client_name}"
            )
            logger.info(f"Sent document receipt notification to owner {owner_telegram_id} for subscription {client_sub_id}")
        except Exception as e:
            logger.error(f"Failed to send document receipt notification to owner {owner_telegram_id}: {e}")
    else:
        logger.warning(f"No owner found for business {business_id}")

    await state.clear()

    await message.answer(
        f"✅ <b>Чек прикреплен, заявка отправлена!</b>\n\n"
        f"🎫 Абонемент: {subscription_name}\n\n"
        f"Ожидайте подтверждения оплаты от владельца.\n"
        f"Вы получите уведомление, когда абонемент будет активирован.",
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("cancel_sub_purchase:"))
async def cancel_subscription_purchase(callback: CallbackQuery, state: FSMContext):
    """Отмена покупки абонемента"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if cs:
            business_id = cs.business_id
            await update_client_subscription_status(session, client_sub_id, "cancelled")
        else:
            business_id = None

    await state.clear()

    if business_id:
        from aiogram.utils.keyboard import InlineKeyboardBuilder
        builder = InlineKeyboardBuilder()
        builder.button(text="🎫 К абонементам", callback_data=f"view_subscriptions:{business_id}")
        builder.button(text="🏠 Главное меню", callback_data="client_main_menu")
        builder.adjust(1)

        await callback.message.edit_text(
            "❌ Покупка абонемента отменена",
            reply_markup=builder.as_markup()
        )
    else:
        await callback.message.edit_text("❌ Покупка абонемента отменена")

    await callback.answer()


# ===== МОИ АБОНЕМЕНТЫ =====

@router.message(F.text == "🎫 Мои абонементы")
async def show_my_subscriptions(message: Message, state: FSMContext):
    """Показать абонементы клиента"""
    await state.clear()

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=message.from_user.id,
            username=message.from_user.username,
            full_name=message.from_user.full_name
        )

        client_subs = await get_client_subscriptions(session, user.id)

    if not client_subs:
        await message.answer(
            "У вас пока нет абонементов.\n\n"
            "Вы можете приобрести абонементы при записи - "
            "выберите бизнес и нажмите '🎫 Абонементы'.",
        )
        return

    await message.answer(
        "🎫 <b>Мои абонементы</b>\n\n"
        "Выберите абонемент для просмотра деталей:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_my_subscriptions_keyboard(client_subs)
    )


@router.callback_query(F.data.startswith("my_subscriptions"))
async def my_subscriptions_callback(callback: CallbackQuery, state: FSMContext):
    """Мои абонементы (callback)"""
    await state.clear()

    # Парсим страницу: my_subscriptions или my_subscriptions:page:X
    parts = callback.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[1] == "page" else 0

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        client_subs = await get_client_subscriptions(session, user.id)

    if not client_subs:
        await callback.message.edit_text(
            "У вас пока нет абонементов.\n\n"
            "Вы можете приобрести абонементы при записи - "
            "выберите бизнес и нажмите '🎫 Абонементы'."
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🎫 <b>Мои абонементы</b>\n\n"
        "Выберите абонемент для просмотра деталей:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_my_subscriptions_keyboard(client_subs, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("my_sub_details:"))
async def my_subscription_details(callback: CallbackQuery, state: FSMContext):
    """Детали моего абонемента"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if not cs:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        remaining = await get_subscription_remaining(session, client_sub_id)

    status_text = {
        "pending_payment": "⏳ Ожидает оплаты",
        "payment_sent": "💳 Оплата отправлена, ожидает подтверждения",
        "active": "✅ Активен",
        "expired": "⌛ Срок действия истек",
        "depleted": "📭 Услуги израсходованы",
        "cancelled": "❌ Отменен"
    }.get(cs.status, cs.status)

    # Информация об остатке услуг
    remaining_text = ""
    if remaining:
        for service_id, info in remaining.items():
            remaining_text += f"  • {info['service_name']}: {info['remaining']}/{info['total']}\n"

    text = (
        f"🎫 <b>{cs.subscription.name}</b>\n\n"
        f"🏢 {cs.business.name}\n"
        f"📊 Статус: {status_text}\n"
        f"📅 Куплен: {cs.purchased_at.strftime('%d.%m.%Y')}\n"
    )

    if cs.status == "active" and cs.expires_at:
        days_left = (cs.expires_at - utc_now()).days
        if days_left >= 0:
            text += f"⏰ Истекает: {cs.expires_at.strftime('%d.%m.%Y')} (осталось {days_left} дн.)\n"
        else:
            text += f"⌛ Истёк: {cs.expires_at.strftime('%d.%m.%Y')} ({abs(days_left)} дн. назад)\n"

    if remaining_text:
        text += f"\n📋 Остаток услуг:\n{remaining_text}"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_my_subscription_details_keyboard(
            client_sub_id,
            cs.status
        )
    )
    await callback.answer()


# ===== ИСПОЛЬЗОВАНИЕ АБОНЕМЕНТА ПРИ БРОНИРОВАНИИ =====

@router.callback_query(F.data.startswith("use_subscription:"))
async def choose_subscription_for_booking(callback: CallbackQuery, state: FSMContext):
    """Выбор услуги из абонемента для бронирования"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    page = int(parts[3]) if len(parts) > 3 and parts[2] == "page" else 0

    async with async_session_maker() as session:
        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        available_services = await get_client_available_services(
            session,
            user.id,
            business_id
        )

    if not available_services:
        await callback.answer(
            "У вас нет активных абонементов с доступными услугами для этого бизнеса",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "🎫 <b>Использовать абонемент</b>\n\n"
        "Выберите услугу из абонемента для записи:\n"
        "(В скобках указано оставшееся количество)",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_services_for_booking_keyboard(
            available_services,
            business_id,
            page
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("use_sub_svc:"))
async def subscription_service_chosen(callback: CallbackQuery, state: FSMContext):
    """Выбрана конкретная услуга из абонемента — переходим к обычному выбору мастера/даты/времени"""
    parts = callback.data.split(":")
    client_subscription_id = int(parts[1])
    service_id = int(parts[2])

    async with async_session_maker() as session:
        client_sub = await get_client_subscription_by_id(session, client_subscription_id)
        if not client_sub:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        user = await get_or_create_user(
            session,
            telegram_id=callback.from_user.id,
            username=callback.from_user.username,
            full_name=callback.from_user.full_name
        )

        if client_sub.client_id != user.id:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        service = await get_service_by_id(session, service_id)
        if not service:
            await callback.answer("Услуга не найдена", show_alert=True)
            return

        employees = await get_business_employees(session, client_sub.business_id)

    await state.update_data(
        business_id=client_sub.business_id,
        business_name=client_sub.business.name,
        service_id=service_id,
        service_name=service.name,
        service_duration=service.duration_minutes,
        service_price=None,
        client_subscription_id=client_subscription_id,
        subscription_name=client_sub.subscription.name,
    )

    await callback.message.edit_text(
        f"🎫 Абонемент: {client_sub.subscription.name}\n"
        f"Услуга: {service.name}\n"
        f"Длительность: {service.duration_minutes} мин\n\n"
        "Выберите специалиста:",
        reply_markup=client_kb.get_employees_keyboard(employees)
    )
    await state.set_state(BookingStates.choosing_employee)
    await callback.answer()


@router.callback_query(F.data.startswith("regular_booking:"))
async def switch_to_regular_booking(callback: CallbackQuery, state: FSMContext):
    """Переключение на обычную запись (без абонемента)"""
    business_id = int(callback.data.split(":")[1])

    # Возвращаемся к выбору услуг
    await state.update_data(use_subscription=False)

    # Переходим к списку услуг того же бизнеса. aiogram не переотправляет
    # апдейт сам по себе просто из-за смены callback.data постфактум —
    # мутируем его под формат, который ждёт целевой хендлер, и вызываем
    # хендлер напрямую (тот же паттерн, что уже используется в admin.py:
    # manage_additional_services/edit_service_menu/show_duty_calendar).
    from bot.handlers.client import continue_booking_after_info
    callback.data = f"continue_booking:{business_id}"
    await continue_booking_after_info(callback, state)
