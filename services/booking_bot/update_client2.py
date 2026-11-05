with open('bot/handlers/client.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 3. Обновляем employee_chosen - даты на месяц
old3 = '''    # Генерируем доступные даты (включая сегодня + следующие 7 дней)
    available_dates = []
    for i in range(0, 8):  # Начинаем с 0 чтобы включить сегодня
        future_date = datetime.now().date() + timedelta(days=i)
        if i == 0:
            date_str = f"Сегодня ({future_date.strftime('%d.%m')})"
        elif i == 1:
            date_str = f"Завтра ({future_date.strftime('%d.%m')})"
        else:
            date_str = future_date.strftime("%d.%m.%Y")
        available_dates.append(date_str)'''

new3 = '''    # Названия дней недели
    weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    
    # Генерируем даты на месяц вперед
    available_dates = []
    for i in range(0, 31):
        future_date = datetime.now().date() + timedelta(days=i)
        weekday = weekdays[future_date.weekday()]
        day = future_date.day
        display = "{} {}".format(day, weekday)
        value = future_date.strftime("%d.%m.%Y")
        available_dates.append({'display': display, 'value': value})'''

content = content.replace(old3, new3)

# 4. Обновляем date_chosen - убираем парсинг "Сегодня"/"Завтра"
old4 = '''    # Парсим дату из разных форматов
    if date_str_raw.startswith("Сегодня"):
        selected_date = datetime.now().date()
        date_str = selected_date.strftime("%d.%m.%Y")
    elif date_str_raw.startswith("Завтра"):
        selected_date = datetime.now().date() + timedelta(days=1)
        date_str = selected_date.strftime("%d.%m.%Y")
    else:
        date_str = date_str_raw
        selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()'''

new4 = '''    # Парсим дату (формат dd.mm.yyyy)
    date_str = date_str_raw
    selected_date = datetime.strptime(date_str, "%d.%m.%Y").date()'''

content = content.replace(old4, new4)

# 5. Обновляем расчет длительности в date_chosen
old5 = '''        # Получаем услугу для учета длительности
        service = None
        service_duration = 60  # По умолчанию 60 минут
        if service_id:
            from database.crud import get_service_by_id
            service = await get_service_by_id(session, service_id)
            if service:
                service_duration = service.duration_minutes'''

new5 = '''        # Используем общую длительность выбранных услуг
        service_duration = data.get('total_duration', 60)'''

content = content.replace(old5, new5)

with open('bot/handlers/client.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Part 2 done")
