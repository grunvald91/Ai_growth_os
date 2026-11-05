with open('bot/handlers/client.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 7. Обновляем booking_confirmed - создание записи
old7 = '''        try:
            # Создаем запись
            booking = await create_booking(
                session,
                business_id=data['business_id'],
                client_id=user.id,
                service_id=data['service_id'],
                booking_date=booking_date,
                booking_time=booking_time,
                employee_id=data.get('employee_id')
            )'''

new7 = '''        # Получаем список услуг
        services_info = data.get('selected_services_info', [])
        if not services_info:
            services_info = [{
                'id': data.get('service_id'),
                'name': data.get('service_name'),
                'duration': data.get('service_duration', 60),
                'price': data.get('service_price', 0)
            }]

        created_bookings = []
        current_time = datetime.combine(booking_date, booking_time)

        try:
            # Создаем записи для каждой услуги
            for svc in services_info:
                booking = await create_booking(
                    session,
                    business_id=data['business_id'],
                    client_id=user.id,
                    service_id=svc['id'],
                    booking_date=booking_date,
                    booking_time=current_time.time(),
                    employee_id=data.get('employee_id')
                )
                created_bookings.append(booking)
                current_time += timedelta(minutes=svc['duration'])
            
            booking = created_bookings[0]'''

content = content.replace(old7, new7)

# 8. Обновляем текст подтверждения
old8 = '''    confirmation_text = f"""
✅ <b>Запись успешно создана!</b>

📅 {data['booking_date']} в {data['booking_time']}
🏢 {data['business_name']}
💼 {data['service_name']}
{address_text}
{phone_text}

{reminder_text}
"""'''

new8 = '''    # Формируем список услуг
    services_info = data.get('selected_services_info', [])
    if not services_info:
        services_info = [{'name': data.get('service_name', 'Услуга')}]
    
    svc_lines = ["  - {}".format(s['name']) for s in services_info]
    services_list = "\n".join(svc_lines)
    total_duration = data.get('total_duration', data.get('service_duration', 0))
    total_price = data.get('total_price', data.get('service_price', 0))
    price_line = "💰 Итого: {}₽\n".format(total_price) if total_price else ""

    confirmation_text = """
✅ <b>Запись успешно создана!</b>

📅 {} в {}
🏢 {}
💼 Услуги:
{}
⏱ Общее время: {} мин
{}{}
{}

{}
""".format(
        data['booking_date'],
        data['booking_time'],
        data['business_name'],
        services_list,
        total_duration,
        price_line,
        address_text,
        phone_text,
        reminder_text
    )'''

content = content.replace(old8, new8)

with open('bot/handlers/client.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Part 4 done")
