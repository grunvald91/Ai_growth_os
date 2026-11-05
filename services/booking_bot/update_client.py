import re

with open('bot/handlers/client.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Обновляем continue_booking_after_info
old1 = '''    await callback.message.edit_text(
        f"Выбрано: {business.name}\n\nВыберите услугу:",
        reply_markup=client_kb.get_services_keyboard(services)
    )
    await state.set_state(BookingStates.choosing_service)'''

new1 = '''    # Инициализируем список выбранных услуг
    await state.update_data(selected_services=[])
    
    await callback.message.edit_text(
        f"Выбрано: {business.name}\n\nВыберите услуги (можно несколько):",
        reply_markup=client_kb.get_services_keyboard(services, selected_ids=[])
    )
    await state.set_state(BookingStates.choosing_service)'''

content = content.replace(old1, new1)

# 2. Обновляем service_chosen для мультивыбора
old2 = '''@router.callback_query(F.data.startswith("service:"), BookingStates.choosing_service)
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
    await callback.answer()'''

new2 = '''@router.callback_query(F.data.startswith("service:"), BookingStates.choosing_service)
async def service_chosen(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора услуги (мультивыбор)"""
    service_id = int(callback.data.split(":")[1])

    data = await state.get_data()
    business_id = data["business_id"]
    selected_services = data.get("selected_services", [])

    # Добавляем или удаляем услугу из списка
    if service_id in selected_services:
        selected_services.remove(service_id)
    else:
        selected_services.append(service_id)

    await state.update_data(selected_services=selected_services)

    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)
        business = await get_business_by_id(session, business_id)

    # Формируем текст с выбранными услугами
    if selected_services:
        selected_names = []
        total_duration = 0
        total_price = 0
        for s in services:
            if s.id in selected_services:
                selected_names.append(s.name)
                total_duration += s.duration_minutes
                if s.price:
                    total_price += s.price
        services_text = "\n\n<b>Выбрано ({}):</b>\n".format(len(selected_services))
        services_text += "\n".join(["- " + name for name in selected_names])
        services_text += "\n\nОбщее время: {} мин".format(total_duration)
        if total_price:
            services_text += "\nИтого: {}р".format(total_price)
    else:
        services_text = ""

    await callback.message.edit_text(
        f"{business.name}\n\nВыберите услуги:{services_text}",
        parse_mode="HTML",
        reply_markup=client_kb.get_services_keyboard(services, selected_ids=selected_services)
    )
    await callback.answer()


@router.callback_query(F.data == "services_done", BookingStates.choosing_service)
async def services_done(callback: CallbackQuery, state: FSMContext):
    """Завершение выбора услуг"""
    data = await state.get_data()
    business_id = data["business_id"]
    selected_services = data.get("selected_services", [])

    if not selected_services:
        await callback.answer("Выберите хотя бы одну услугу", show_alert=True)
        return

    async with async_session_maker() as session:
        services = await get_business_services(session, business_id)
        employees = await get_business_employees(session, business_id)

        selected_info = []
        total_duration = 0
        total_price = 0
        for s in services:
            if s.id in selected_services:
                selected_info.append({
                    'id': s.id,
                    'name': s.name,
                    'duration': s.duration_minutes,
                    'price': s.price or 0
                })
                total_duration += s.duration_minutes
                if s.price:
                    total_price += s.price

    await state.update_data(
        selected_services_info=selected_info,
        total_duration=total_duration,
        total_price=total_price
    )

    services_text = "\n".join(["- {} ({} мин)".format(info['name'], info['duration']) for info in selected_info])

    await callback.message.edit_text(
        "<b>Выбранные услуги:</b>\n{}\n\nОбщее время: {} мин\n{}\n\nВыберите специалиста:".format(
            services_text, total_duration, 
            "Итого: {}р".format(total_price) if total_price else ""
        ),
        parse_mode="HTML",
        reply_markup=client_kb.get_employees_keyboard(employees)
    )
    await state.set_state(BookingStates.choosing_employee)
    await callback.answer()'''

content = content.replace(old2, new2)

with open('bot/handlers/client.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Part 1 done")
