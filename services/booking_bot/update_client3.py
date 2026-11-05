with open('bot/handlers/client.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 6. Обновляем show_confirmation
old6 = '''async def show_confirmation(message: Message, state: FSMContext):
    """Показ экрана подтверждения записи"""
    data = await state.get_data()

    confirmation_text = f"""
📋 <b>Подтверждение записи</b>

🏢 Заведение: {data['business_name']}
💼 Услуга: {data['service_name']}
⏱ Длительность: {data['service_duration']} мин
{f"💰 Цена: {data['service_price']}₽" if data.get('service_price') else ""}
👤 Мастер: {data['employee_name']}
📅 Дата: {data['booking_date']}
🕐 Время: {data['booking_time']}

Подтверждаете запись?
"""'''

new6 = '''async def show_confirmation(message: Message, state: FSMContext):
    """Показ экрана подтверждения записи"""
    data = await state.get_data()

    # Формируем список услуг
    services_info = data.get('selected_services_info', [])
    if services_info:
        svc_lines = ["  - {} ({} мин)".format(s['name'], s['duration']) for s in services_info]
        services_text = "\n".join(svc_lines)
        total_duration = data.get('total_duration', 0)
        total_price = data.get('total_price', 0)
    else:
        services_text = "  - {}".format(data.get('service_name', 'Услуга'))
        total_duration = data.get('service_duration', 0)
        total_price = data.get('service_price', 0)

    price_text = "💰 Итого: {}₽\n".format(total_price) if total_price else ""

    confirmation_text = """
📋 <b>Подтверждение записи</b>

🏢 Заведение: {}
💼 Услуги:
{}
⏱ Общее время: {} мин
{}👤 Мастер: {}
📅 Дата: {}
🕐 Время: {}

Подтверждаете запись?
""".format(
        data['business_name'],
        services_text,
        total_duration,
        price_text,
        data['employee_name'],
        data['booking_date'],
        data['booking_time']
    )'''

content = content.replace(old6, new6)

with open('bot/handlers/client.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Part 3 done")
