"""
Обработчики для управления абонементами (владелец бизнеса)
"""
from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from database import async_session_maker
from database.crud import (
    get_or_create_user,
    get_business_by_id,
    get_business_services,
    get_business_subscriptions,
    get_subscription_by_id,
    create_subscription,
    update_subscription,
    update_subscription_services,
    delete_subscription,
    get_sold_subscriptions,
    get_pending_payment_subscriptions,
    get_client_subscription_by_id,
    confirm_subscription_payment,
    update_client_subscription_status,
    get_subscription_remaining,
    is_business_owner,
)
from bot.states.subscription import SubscriptionCreationStates, SubscriptionEditStates
from bot.keyboards import subscription_kb
from bot.utils.business_types import get_terminology
from bot.utils.message_utils import safe_edit_or_send

router = Router()


# ===== УПРАВЛЕНИЕ АБОНЕМЕНТАМИ =====

@router.callback_query(F.data.startswith("manage_subscriptions:"))
async def manage_subscriptions(callback: CallbackQuery, state: FSMContext):
    """Управление абонементами бизнеса"""
    await state.clear()
    business_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        business = await get_business_by_id(session, business_id)
        if not business:
            await callback.answer("Бизнес не найден", show_alert=True)
            return

    term = get_terminology(business.business_type)

    await safe_edit_or_send(
        callback,
        f"🎫 <b>Управление абонементами</b>\n\n"
        f"Бизнес: {business.name}\n\n"
        f"Абонементы позволяют вашим клиентам покупать пакеты "
        f"{'времени аренды' if term.get('is_venue') else 'услуг'} по выгодной цене.",
        reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("list_subscriptions:"))
async def list_subscriptions(callback: CallbackQuery, state: FSMContext):
    """Список абонементов бизнеса"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    page = int(parts[3]) if len(parts) > 3 and parts[2] == "page" else 0

    async with async_session_maker() as session:
        subscriptions = await get_business_subscriptions(session, business_id, active_only=False)
        business = await get_business_by_id(session, business_id)

    if not subscriptions:
        await safe_edit_or_send(
            callback,
            "У вас пока нет ни одного абонемента.\n"
            "Создайте абонемент, чтобы клиенты могли покупать пакеты услуг.",
            reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
        )
        await callback.answer()
        return

    await safe_edit_or_send(
        callback,
        f"📋 <b>Абонементы {business.name}</b>\n\n"
        f"Всего: {len(subscriptions)} шт.",
        reply_markup=subscription_kb.get_subscriptions_list_keyboard(subscriptions, business_id, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_subscription:"))
async def view_subscription(callback: CallbackQuery, state: FSMContext):
    """Просмотр абонемента"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        subscription = await get_subscription_by_id(session, subscription_id)
        if not subscription:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

    # Формируем список услуг
    services_text = ""
    for sub_svc in subscription.services:
        services_text += f"  • {sub_svc.service.name} x{sub_svc.quantity}\n"

    text = (
        f"🎫 <b>{subscription.name}</b>\n\n"
        f"💰 Цена: {subscription.price}₽\n"
        f"📅 Срок действия: {subscription.validity_days} дней\n"
        f"📊 Статус: {'✅ Активен' if subscription.is_active else '⏸ Неактивен'}\n\n"
    )

    if subscription.description:
        text += f"📝 Описание:\n{subscription.description}\n\n"

    text += f"📋 Услуги:\n{services_text}\n"

    if subscription.rules:
        text += f"📖 Правила:\n{subscription.rules}\n"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription_id,
            subscription.business_id,
            subscription.is_active
        )
    )
    await callback.answer()


# ===== СОЗДАНИЕ АБОНЕМЕНТА =====

@router.callback_query(F.data.startswith("create_subscription:"))
async def start_subscription_creation(callback: CallbackQuery, state: FSMContext):
    """Начало создания абонемента"""
    business_id = int(callback.data.split(":")[1])

    # Проверяем, есть ли услуги у бизнеса
    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)
        if not services:
            await callback.answer(
                "Сначала создайте услуги для бизнеса",
                show_alert=True
            )
            return

    await state.update_data(
        business_id=business_id,
        selected_services=[],
        service_quantities={}
    )

    await callback.message.edit_text(
        "🎫 <b>Создание абонемента</b>\n\n"
        "Шаг 1/7: Введите название абонемента\n\n"
        "Например: \"Безлимит на месяц\", \"10 стрижек\", \"Пакет релакс\"",
        parse_mode="HTML"
    )
    await state.set_state(SubscriptionCreationStates.entering_name)
    await callback.answer()


@router.message(SubscriptionCreationStates.entering_name)
async def subscription_name_entered(message: Message, state: FSMContext):
    """Получение названия абонемента"""
    await state.update_data(name=message.text)

    await message.answer(
        "🎫 <b>Создание абонемента</b>\n\n"
        "Шаг 2/7: Введите описание абонемента\n\n"
        "Опишите, что входит в абонемент и его преимущества.\n"
        "Отправьте '-' чтобы пропустить.",
        parse_mode="HTML"
    )
    await state.set_state(SubscriptionCreationStates.entering_description)


@router.message(SubscriptionCreationStates.entering_description)
async def subscription_description_entered(message: Message, state: FSMContext):
    """Получение описания абонемента"""
    description = None if message.text == "-" else message.text
    await state.update_data(description=description)

    data = await state.get_data()
    business_id = data["business_id"]

    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)

    await message.answer(
        "🎫 <b>Создание абонемента</b>\n\n"
        "Шаг 3/7: Выберите услуги для абонемента\n\n"
        "Отметьте все услуги, которые будут входить в абонемент:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_services_selection_keyboard(
            services,
            [],
            business_id
        )
    )
    await state.set_state(SubscriptionCreationStates.selecting_services)


@router.callback_query(F.data.startswith("sub_toggle_svc:"), SubscriptionCreationStates.selecting_services)
async def toggle_subscription_service(callback: CallbackQuery, state: FSMContext):
    """Переключение выбора услуги для абонемента"""
    service_id = int(callback.data.split(":")[1])
    data = await state.get_data()

    selected = data.get("selected_services", [])
    if service_id in selected:
        selected.remove(service_id)
    else:
        selected.append(service_id)

    await state.update_data(selected_services=selected)

    business_id = data["business_id"]
    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)

    await callback.message.edit_reply_markup(
        reply_markup=subscription_kb.get_subscription_services_selection_keyboard(
            services,
            selected,
            business_id
        )
    )
    await callback.answer()


@router.callback_query(F.data == "sub_services_done", SubscriptionCreationStates.selecting_services)
async def subscription_services_done(callback: CallbackQuery, state: FSMContext):
    """Переход к вводу количества услуг"""
    data = await state.get_data()
    selected = data.get("selected_services", [])

    if not selected:
        await callback.answer("Выберите хотя бы одну услугу", show_alert=True)
        return

    # Инициализируем количества (по умолчанию 1)
    quantities = {sid: 1 for sid in selected}
    await state.update_data(
        service_quantities=quantities,
        current_service_index=0
    )

    # Получаем информацию о первой услуге
    async with async_session_maker() as session:
        services = await get_business_services(session, data["business_id"])
        services_dict = {s.id: s for s in services}

    first_service_id = selected[0]
    first_service = services_dict[first_service_id]

    await callback.message.edit_text(
        f"🎫 <b>Создание абонемента</b>\n\n"
        f"Шаг 4/7: Количество услуг\n\n"
        f"Услуга: <b>{first_service.name}</b>\n"
        f"Укажите количество этой услуги в абонементе:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_quantity_keyboard(
            first_service.name,
            1,
            first_service_id
        )
    )
    await state.set_state(SubscriptionCreationStates.entering_quantities)
    await callback.answer()


@router.callback_query(F.data.startswith("sub_qty_"), SubscriptionCreationStates.entering_quantities)
async def subscription_quantity_change(callback: CallbackQuery, state: FSMContext):
    """Изменение количества услуги"""
    action, service_id = callback.data.split(":")[0].split("_")[-1], int(callback.data.split(":")[1])
    data = await state.get_data()

    quantities = data.get("service_quantities", {})
    current_qty = quantities.get(service_id, 1)

    if action == "inc":
        current_qty = min(99, current_qty + 1)
    elif action == "dec":
        current_qty = max(1, current_qty - 1)
    elif action == "save":
        # Переход к следующей услуге или к цене
        selected = data.get("selected_services", [])
        current_index = data.get("current_service_index", 0)

        if current_index + 1 < len(selected):
            # Есть ещё услуги
            next_index = current_index + 1
            next_service_id = selected[next_index]

            async with async_session_maker() as session:
                services = await get_business_services(session, data["business_id"])
                services_dict = {s.id: s for s in services}

            next_service = services_dict[next_service_id]
            next_qty = quantities.get(next_service_id, 1)

            await state.update_data(current_service_index=next_index)

            await callback.message.edit_text(
                f"🎫 <b>Создание абонемента</b>\n\n"
                f"Шаг 4/7: Количество услуг ({next_index + 1}/{len(selected)})\n\n"
                f"Услуга: <b>{next_service.name}</b>\n"
                f"Укажите количество этой услуги в абонементе:",
                parse_mode="HTML",
                reply_markup=subscription_kb.get_subscription_quantity_keyboard(
                    next_service.name,
                    next_qty,
                    next_service_id
                )
            )
        else:
            # Все услуги введены, переходим к цене
            await callback.message.edit_text(
                "🎫 <b>Создание абонемента</b>\n\n"
                "Шаг 5/7: Введите цену абонемента (в рублях)\n\n"
                "Например: 5000",
                parse_mode="HTML"
            )
            await state.set_state(SubscriptionCreationStates.entering_price)

        await callback.answer()
        return

    quantities[service_id] = current_qty
    await state.update_data(service_quantities=quantities)

    async with async_session_maker() as session:
        services = await get_business_services(session, data["business_id"])
        services_dict = {s.id: s for s in services}

    service = services_dict[service_id]

    await callback.message.edit_reply_markup(
        reply_markup=subscription_kb.get_subscription_quantity_keyboard(
            service.name,
            current_qty,
            service_id
        )
    )
    await callback.answer()


@router.message(SubscriptionCreationStates.entering_price)
async def subscription_price_entered(message: Message, state: FSMContext):
    """Получение цены абонемента"""
    try:
        price = int(message.text.replace(" ", "").replace("₽", ""))
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("Введите корректную цену (положительное число):")
        return

    await state.update_data(price=price)

    await message.answer(
        "🎫 <b>Создание абонемента</b>\n\n"
        "Шаг 6/7: Введите срок действия абонемента (в днях)\n\n"
        "Например: 30",
        parse_mode="HTML"
    )
    await state.set_state(SubscriptionCreationStates.entering_validity)


@router.message(SubscriptionCreationStates.entering_validity)
async def subscription_validity_entered(message: Message, state: FSMContext):
    """Получение срока действия"""
    try:
        validity_days = int(message.text)
        if validity_days <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("Введите корректный срок (положительное число дней):")
        return

    await state.update_data(validity_days=validity_days)

    await message.answer(
        "🎫 <b>Создание абонемента</b>\n\n"
        "Шаг 7/7: Введите правила использования абонемента\n\n"
        "Например: \"Только для одного человека\", \"Нельзя совмещать со скидками\"\n"
        "Отправьте '-' чтобы пропустить.",
        parse_mode="HTML"
    )
    await state.set_state(SubscriptionCreationStates.entering_rules)


@router.message(SubscriptionCreationStates.entering_rules)
async def subscription_rules_entered(message: Message, state: FSMContext):
    """Получение правил и показ подтверждения"""
    rules = None if message.text == "-" else message.text
    await state.update_data(rules=rules)

    data = await state.get_data()

    # Формируем текст для подтверждения
    async with async_session_maker() as session:
        services = await get_business_services(session, data["business_id"])
        services_dict = {s.id: s for s in services}

    services_text = ""
    for sid in data["selected_services"]:
        service = services_dict[sid]
        qty = data["service_quantities"].get(sid, 1)
        services_text += f"  • {service.name} x{qty}\n"

    text = (
        f"🎫 <b>Подтверждение создания абонемента</b>\n\n"
        f"📝 Название: {data['name']}\n"
        f"💰 Цена: {data['price']}₽\n"
        f"📅 Срок действия: {data['validity_days']} дней\n\n"
    )

    if data.get("description"):
        text += f"📄 Описание:\n{data['description']}\n\n"

    text += f"📋 Услуги:\n{services_text}\n"

    if data.get("rules"):
        text += f"📖 Правила:\n{data['rules']}\n"

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_creation_confirm_keyboard(data["business_id"])
    )
    await state.set_state(SubscriptionCreationStates.confirming)


@router.callback_query(F.data == "confirm_create_subscription", SubscriptionCreationStates.confirming)
async def confirm_create_subscription(callback: CallbackQuery, state: FSMContext):
    """Подтверждение создания абонемента"""
    data = await state.get_data()

    # Формируем список услуг с количествами
    services_with_quantities = [
        (sid, data["service_quantities"].get(sid, 1))
        for sid in data["selected_services"]
    ]

    async with async_session_maker() as session:
        subscription = await create_subscription(
            session,
            business_id=data["business_id"],
            name=data["name"],
            price=data["price"],
            validity_days=data["validity_days"],
            services_with_quantities=services_with_quantities,
            description=data.get("description"),
            rules=data.get("rules")
        )

    await state.clear()

    await callback.message.edit_text(
        f"✅ Абонемент <b>{data['name']}</b> успешно создан!",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscriptions_management_keyboard(data["business_id"])
    )
    await callback.answer("Абонемент создан!")


# ===== РЕДАКТИРОВАНИЕ АБОНЕМЕНТА =====

@router.callback_query(F.data.startswith("edit_subscription:"))
async def edit_subscription(callback: CallbackQuery, state: FSMContext):
    """Редактирование абонемента"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        subscription = await get_subscription_by_id(session, subscription_id)
        if not subscription:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

    await state.update_data(subscription_id=subscription_id)

    await callback.message.edit_text(
        f"✏️ <b>Редактирование абонемента</b>\n\n"
        f"{subscription.name}\n\n"
        f"Выберите, что хотите изменить:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_subscription_edit_keyboard(
            subscription_id,
            subscription.business_id
        )
    )
    await callback.answer()


@router.callback_query(F.data.startswith("edit_sub_field:"))
async def edit_subscription_field(callback: CallbackQuery, state: FSMContext):
    """Редактирование поля абонемента"""
    parts = callback.data.split(":")
    field = parts[1]
    subscription_id = int(parts[2])

    await state.update_data(subscription_id=subscription_id, edit_field=field)

    field_prompts = {
        "name": "Введите новое название абонемента:",
        "description": "Введите новое описание (или '-' для удаления):",
        "price": "Введите новую цену (в рублях):",
        "validity": "Введите новый срок действия (в днях):",
        "rules": "Введите новые правила (или '-' для удаления):",
    }

    if field == "services":
        # Переход к редактированию услуг
        async with async_session_maker() as session:
            subscription = await get_subscription_by_id(session, subscription_id)
            services = await get_business_services(session, subscription.business_id)
            selected_ids = [ss.service_id for ss in subscription.services]

        await state.update_data(
            business_id=subscription.business_id,
            selected_services=selected_ids,
            service_quantities={ss.service_id: ss.quantity for ss in subscription.services}
        )

        await callback.message.edit_text(
            "✏️ <b>Редактирование услуг абонемента</b>\n\n"
            "Выберите услуги, которые должны входить в абонемент:",
            parse_mode="HTML",
            reply_markup=subscription_kb.get_subscription_services_selection_keyboard(
                services,
                selected_ids,
                subscription.business_id
            )
        )
        await state.set_state(SubscriptionEditStates.editing_services)
    else:
        await callback.message.edit_text(
            f"✏️ <b>Редактирование абонемента</b>\n\n{field_prompts.get(field, 'Введите новое значение:')}",
            parse_mode="HTML"
        )
        state_map = {
            "name": SubscriptionEditStates.editing_name,
            "description": SubscriptionEditStates.editing_description,
            "price": SubscriptionEditStates.editing_price,
            "validity": SubscriptionEditStates.editing_validity,
            "rules": SubscriptionEditStates.editing_rules,
        }
        await state.set_state(state_map.get(field))

    await callback.answer()


@router.message(SubscriptionEditStates.editing_name)
async def subscription_name_edited(message: Message, state: FSMContext):
    """Сохранение нового названия"""
    data = await state.get_data()

    async with async_session_maker() as session:
        await update_subscription(session, data["subscription_id"], name=message.text)
        subscription = await get_subscription_by_id(session, data["subscription_id"])

    await state.clear()
    await message.answer(
        f"✅ Название абонемента изменено на: {message.text}",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription.id,
            subscription.business_id,
            subscription.is_active
        )
    )


@router.message(SubscriptionEditStates.editing_description)
async def subscription_description_edited(message: Message, state: FSMContext):
    """Сохранение нового описания"""
    data = await state.get_data()
    description = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        await update_subscription(session, data["subscription_id"], description=description)
        subscription = await get_subscription_by_id(session, data["subscription_id"])

    await state.clear()
    await message.answer(
        "✅ Описание абонемента обновлено",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription.id,
            subscription.business_id,
            subscription.is_active
        )
    )


@router.message(SubscriptionEditStates.editing_price)
async def subscription_price_edited(message: Message, state: FSMContext):
    """Сохранение новой цены"""
    try:
        price = int(message.text.replace(" ", "").replace("₽", ""))
        if price <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("Введите корректную цену:")
        return

    data = await state.get_data()

    async with async_session_maker() as session:
        await update_subscription(session, data["subscription_id"], price=price)
        subscription = await get_subscription_by_id(session, data["subscription_id"])

    await state.clear()
    await message.answer(
        f"✅ Цена абонемента изменена на: {price}₽",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription.id,
            subscription.business_id,
            subscription.is_active
        )
    )


@router.message(SubscriptionEditStates.editing_validity)
async def subscription_validity_edited(message: Message, state: FSMContext):
    """Сохранение нового срока действия"""
    try:
        validity_days = int(message.text)
        if validity_days <= 0:
            raise ValueError()
    except ValueError:
        await message.answer("Введите корректный срок (в днях):")
        return

    data = await state.get_data()

    async with async_session_maker() as session:
        await update_subscription(session, data["subscription_id"], validity_days=validity_days)
        subscription = await get_subscription_by_id(session, data["subscription_id"])

    await state.clear()
    await message.answer(
        f"✅ Срок действия изменен на: {validity_days} дней",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription.id,
            subscription.business_id,
            subscription.is_active
        )
    )


@router.message(SubscriptionEditStates.editing_rules)
async def subscription_rules_edited(message: Message, state: FSMContext):
    """Сохранение новых правил"""
    data = await state.get_data()
    rules = None if message.text == "-" else message.text

    async with async_session_maker() as session:
        await update_subscription(session, data["subscription_id"], rules=rules)
        subscription = await get_subscription_by_id(session, data["subscription_id"])

    await state.clear()
    await message.answer(
        "✅ Правила абонемента обновлены",
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription.id,
            subscription.business_id,
            subscription.is_active
        )
    )


# ===== АКТИВАЦИЯ/ДЕАКТИВАЦИЯ/УДАЛЕНИЕ =====

@router.callback_query(F.data.startswith("activate_subscription:"))
async def activate_subscription(callback: CallbackQuery):
    """Активировать абонемент"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        await update_subscription(session, subscription_id, is_active=True)
        subscription = await get_subscription_by_id(session, subscription_id)

    await callback.message.edit_reply_markup(
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription_id,
            subscription.business_id,
            True
        )
    )
    await callback.answer("Абонемент активирован")


@router.callback_query(F.data.startswith("deactivate_subscription:"))
async def deactivate_subscription(callback: CallbackQuery):
    """Деактивировать абонемент"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        await update_subscription(session, subscription_id, is_active=False)
        subscription = await get_subscription_by_id(session, subscription_id)

    await callback.message.edit_reply_markup(
        reply_markup=subscription_kb.get_subscription_actions_keyboard(
            subscription_id,
            subscription.business_id,
            False
        )
    )
    await callback.answer("Абонемент деактивирован")


@router.callback_query(F.data.startswith("delete_subscription:"))
async def delete_subscription_handler(callback: CallbackQuery):
    """Удалить абонемент"""
    subscription_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        subscription = await get_subscription_by_id(session, subscription_id)
        if not subscription:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        business_id = subscription.business_id
        await delete_subscription(session, subscription_id)

    await callback.message.edit_text(
        "✅ Абонемент удален",
        reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
    )
    await callback.answer("Абонемент удален")


# ===== ПРОСМОТР ПРОДАННЫХ АБОНЕМЕНТОВ =====

@router.callback_query(F.data.startswith("sold_subscriptions:"))
async def view_sold_subscriptions(callback: CallbackQuery):
    """Просмотр проданных абонементов"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    page = int(parts[3]) if len(parts) > 3 and parts[2] == "page" else 0

    async with async_session_maker() as session:
        client_subscriptions = await get_sold_subscriptions(session, business_id)
        business = await get_business_by_id(session, business_id)

    if not client_subscriptions:
        await callback.message.edit_text(
            "Пока нет проданных абонементов",
            reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
        )
        await callback.answer()
        return

    # Статистика
    active_count = sum(1 for cs in client_subscriptions if cs.status == "active")
    total_revenue = sum(cs.subscription.price for cs in client_subscriptions if cs.status in ["active", "depleted", "expired"])

    await callback.message.edit_text(
        f"📊 <b>Проданные абонементы</b>\n\n"
        f"Бизнес: {business.name}\n"
        f"Активных: {active_count}\n"
        f"Всего продано: {len(client_subscriptions)}\n"
        f"Выручка: {total_revenue}₽",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_sold_subscriptions_keyboard(client_subscriptions, business_id, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_sold_sub:"))
async def view_sold_subscription_details(callback: CallbackQuery):
    """Детали проданного абонемента"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if not cs:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        remaining = await get_subscription_remaining(session, client_sub_id)

    status_text = {
        "pending_payment": "⏳ Ожидает оплаты",
        "payment_sent": "💳 Оплата отправлена",
        "active": "✅ Активен",
        "expired": "⌛ Истек",
        "depleted": "📭 Израсходован"
    }.get(cs.status, cs.status)

    # Информация об остатке услуг
    remaining_text = ""
    for service_id, info in remaining.items():
        remaining_text += f"  • {info['service_name']}: {info['remaining']}/{info['total']}\n"

    text = (
        f"🎫 <b>{cs.subscription.name}</b>\n\n"
        f"👤 Клиент: {cs.client.full_name}\n"
        f"📊 Статус: {status_text}\n"
        f"📅 Куплен: {cs.purchased_at.strftime('%d.%m.%Y')}\n"
        f"⏰ Истекает: {cs.expires_at.strftime('%d.%m.%Y')}\n\n"
        f"📋 Остаток услуг:\n{remaining_text}"
    )

    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    builder.button(text="◀️ Назад", callback_data=f"sold_subscriptions:{cs.business_id}")
    builder.adjust(1)

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await callback.answer()


# ===== ПОДТВЕРЖДЕНИЕ ОПЛАТЫ =====

@router.callback_query(F.data.startswith("pending_sub_payments:"))
async def view_pending_payments(callback: CallbackQuery):
    """Просмотр абонементов, ожидающих подтверждения оплаты"""
    parts = callback.data.split(":")
    business_id = int(parts[1])
    page = int(parts[3]) if len(parts) > 3 and parts[2] == "page" else 0

    async with async_session_maker() as session:
        pending = await get_pending_payment_subscriptions(session, business_id)

    if not pending:
        await callback.message.edit_text(
            "Нет абонементов, ожидающих подтверждения оплаты",
            reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        f"⏳ <b>Ожидают подтверждения оплаты</b>\n\n"
        f"Всего: {len(pending)} шт.\n\n"
        f"Нажмите на абонемент для просмотра деталей и подтверждения:",
        parse_mode="HTML",
        reply_markup=subscription_kb.get_pending_payments_list_keyboard(pending, business_id, page)
    )
    await callback.answer()


@router.callback_query(F.data.startswith("view_pending_sub:"))
async def view_pending_subscription(callback: CallbackQuery):
    """Просмотр ожидающего подтверждения абонемента"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if not cs:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

    text = (
        f"💳 <b>Подтверждение оплаты абонемента</b>\n\n"
        f"🎫 Абонемент: {cs.subscription.name}\n"
        f"💰 Сумма: {cs.subscription.price}₽\n"
        f"👤 Клиент: {cs.client.full_name}\n"
        f"📅 Дата заявки: {cs.purchased_at.strftime('%d.%m.%Y %H:%M')}\n"
    )

    if cs.payment_receipt:
        text += f"\n📎 Чек прикреплен"

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=subscription_kb.get_pending_payment_keyboard(
            client_sub_id,
            cs.business_id
        )
    )

    # Если есть чек - отправляем его отдельным сообщением
    if cs.payment_receipt:
        try:
            await callback.message.answer_photo(
                cs.payment_receipt,
                caption="📎 Чек от клиента"
            )
        except:
            try:
                await callback.message.answer_document(
                    cs.payment_receipt,
                    caption="📎 Чек от клиента"
                )
            except:
                pass

    await callback.answer()


@router.callback_query(F.data.startswith("confirm_sub_payment:"))
async def confirm_sub_payment(callback: CallbackQuery):
    """Подтверждение оплаты абонемента владельцем"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await confirm_subscription_payment(session, client_sub_id)
        if not cs:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

    # Уведомление клиенту
    try:
        await callback.bot.send_message(
            cs.client.telegram_id,
            f"✅ <b>Оплата абонемента подтверждена!</b>\n\n"
            f"🎫 Абонемент: {cs.subscription.name}\n"
            f"📅 Действует до: {cs.expires_at.strftime('%d.%m.%Y')}\n\n"
            f"Теперь вы можете использовать услуги из абонемента при записи.",
            parse_mode="HTML"
        )
    except:
        pass

    await callback.message.edit_text(
        f"✅ Оплата подтверждена!\n\n"
        f"Абонемент активирован до {cs.expires_at.strftime('%d.%m.%Y')}",
        reply_markup=subscription_kb.get_subscriptions_management_keyboard(cs.business_id)
    )
    await callback.answer("Оплата подтверждена!")


@router.callback_query(F.data.startswith("reject_sub_payment:"))
async def reject_sub_payment(callback: CallbackQuery):
    """Отклонение оплаты абонемента"""
    client_sub_id = int(callback.data.split(":")[1])

    async with async_session_maker() as session:
        cs = await get_client_subscription_by_id(session, client_sub_id)
        if not cs:
            await callback.answer("Абонемент не найден", show_alert=True)
            return

        business_id = cs.business_id
        client_telegram_id = cs.client.telegram_id
        subscription_name = cs.subscription.name

        # Удаляем запись о покупке
        await update_client_subscription_status(session, client_sub_id, "cancelled")

    # Уведомление клиенту
    try:
        await callback.bot.send_message(
            client_telegram_id,
            f"❌ <b>Оплата абонемента не подтверждена</b>\n\n"
            f"🎫 Абонемент: {subscription_name}\n\n"
            f"Владелец не получил оплату. Если вы оплатили, "
            f"свяжитесь с владельцем для уточнения.",
            parse_mode="HTML"
        )
    except:
        pass

    await callback.message.edit_text(
        "❌ Оплата отклонена, клиент уведомлен",
        reply_markup=subscription_kb.get_subscriptions_management_keyboard(business_id)
    )
    await callback.answer("Оплата отклонена")
