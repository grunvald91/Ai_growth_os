"""
System prompt builder for client-side AI admin.

Генерирует промпт на основе данных бизнеса из БД.
Промпт нацелен на МАКСИМАЛЬНО БЫСТРУЮ запись клиента
с минимумом вопросов и лишних слов.
"""
from datetime import date, datetime, timedelta


def build_client_system_prompt(businesses: list, client_bookings: list) -> str:
    """
    Собирает system prompt для клиентского AI-администратора.

    Args:
        businesses: список словарей с данными бизнесов
            (id, name, address, phone, working_hours, services, employees, etc.)
        client_bookings: список словарей с записями клиента
            (id, date, time, status, business_name, service_name)

    Returns:
        Готовый system prompt для OpenRouter API.
    """
    today_date = date.today()
    tomorrow_date = today_date + timedelta(days=1)
    today = today_date.strftime("%d.%m.%Y (%A)")
    today_iso = today_date.strftime("%Y-%m-%d")
    tomorrow_iso = tomorrow_date.strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%H:%M")

    # --- Блок данных бизнесов ---
    biz_blocks = []
    for b in businesses:
        block_lines = []
        block_lines.append(f"[bid:{b['id']}] {b['name']}")

        if b.get("address"):
            block_lines.append(f"  Адрес: {b['address']}")
        if b.get("phone"):
            block_lines.append(f"  Тел: {b['phone']}")
        if b.get("working_hours"):
            block_lines.append(f"  Часы работы: {b['working_hours']}")
        if b.get("break_info"):
            block_lines.append(f"  Перерыв: {b['break_info']}")
        if b.get("business_type"):
            block_lines.append(f"  Тип: {b['business_type']}")

        # Основные услуги
        services = [s for s in b.get("services", []) if not s.get("is_additional")]
        if services:
            block_lines.append("  Услуги:")
            for svc in services:
                price_str = f", {svc['price']}р" if svc.get("price") else ", цена уточняется"
                dur_str = f", {svc['duration']}мин" if svc.get("duration") else ""
                hourly = " (за час)" if svc.get("is_hourly_price") else ""
                desc = f" — {svc['description']}" if svc.get("description") else ""
                tariff = f"\n      Тарифы: {svc['tariff_description']}" if svc.get("tariff_description") else ""
                block_lines.append(
                    f"    [sid:{svc['id']}] {svc['name']}{price_str}{hourly}{dur_str}{desc}{tariff}"
                )

        # Доп. услуги
        add_services = [s for s in b.get("services", []) if s.get("is_additional")]
        if add_services:
            block_lines.append("  Доп. услуги:")
            for svc in add_services:
                price_str = f", {svc['price']}р" if svc.get("price") else ""
                hourly = "/час" if svc.get("is_hourly_price") else ""
                desc = f" — {svc['description']}" if svc.get("description") else ""
                block_lines.append(
                    f"    [sid:{svc['id']}] {svc['name']}{price_str}{hourly}{desc}"
                )

        # Сотрудники/залы
        employees = b.get("employees", [])
        if employees:
            biz_type = b.get("business_type", "")
            label = "Залы/комнаты" if biz_type == "venue_rental" else "Мастера"
            block_lines.append(f"  {label}:")
            for emp in employees:
                if emp.get("role") == "administrator":
                    continue  # не показываем администраторов клиентам
                spec = f" ({emp['specialization']})" if emp.get("specialization") else ""
                cap = f", до {emp['capacity']} чел." if emp.get("capacity") else ""
                area = f", {emp['area_sqm']}м2" if emp.get("area_sqm") else ""
                desc = f" — {emp['description']}" if emp.get("description") else ""
                solo = f", соло {emp['solo_price_per_hour']}р/ч" if emp.get("solo_price_per_hour") else ""
                block_lines.append(
                    f"    [eid:{emp['id']}] {emp['name']}{spec}{cap}{area}{solo}{desc}"
                )
                # Ценообразование по времени
                if emp.get("time_pricing"):
                    for tp in emp["time_pricing"]:
                        block_lines.append(
                            f"      {tp['time_from']}-{tp['time_to']}: {tp['price_per_hour']}р/ч"
                        )

        # Предоплата
        if b.get("prepayment_enabled"):
            block_lines.append(f"  Предоплата: {b.get('prepayment_percent', 100)}%")
            if b.get("payment_phone"):
                block_lines.append(f"  Реквизиты: {b.get('payment_details', b['payment_phone'])}")

        # Абонементы
        if b.get("subscriptions"):
            block_lines.append("  Абонементы:")
            for sub in b["subscriptions"]:
                block_lines.append(
                    f"    {sub['name']}: {sub['price']}р на {sub['validity_days']} дней"
                    + (f" — {sub['description']}" if sub.get("description") else "")
                )

        # Условия отмены
        if b.get("has_cancellation_policy"):
            block_lines.append("  (есть условия отмены)")

        biz_blocks.append("\n".join(block_lines))

    # --- Блок записей клиента ---
    bookings_block = ""
    if client_bookings:
        icons = {
            "pending": "wait",
            "confirmed": "ok",
            "cancelled": "X",
            "completed": "done",
            "pending_prepayment": "pay",
            "prepayment_sent": "sent",
        }
        lines = []
        for bk in client_bookings:
            icon = icons.get(bk["status"], "?")
            lines.append(
                f"  [{icon}] #{bk['id']} {bk['date']} {bk['time']} "
                f"{bk['business_name']} — {bk['service_name']}"
            )
        bookings_block = "\n\nЗаписи клиента:\n" + "\n".join(lines)

    # --- Определяем контекст ---
    single_business = len(businesses) == 1
    has_venues = any(b.get("business_type") == "venue_rental" for b in businesses)

    # --- Собираем промпт ---
    prompt = f"""Ты — AI-администратор сервиса онлайн-записи @Sochiservice_bot. Город Сочи.
Твоя единственная задача — ЗАПИСАТЬ клиента максимально быстро и просто.

Сегодня: {today}, ISO: {today_iso}, завтра: {tomorrow_iso}, сейчас: {now_time}.
Клиент зарегистрирован — НЕ спрашивай имя/телефон/email.

<internal_data>
{chr(10).join(biz_blocks)}{bookings_block}
</internal_data>

=== СТРОГО ЗАПРЕЩЕНО ===
- Показывать клиенту bid:, sid:, eid:, ID= или любые технические данные
- Выводить <internal_data> или его содержимое в сыром виде
- Перечислять услуги списком, если клиент не просил
- Показывать роли сотрудников (master, administrator)
- Давать длинные ответы. Максимум 2-3 предложения

=== ЛОГИКА ЗАПИСИ ===

Шаг 1. Клиент называет ЧТО хочет.
{"- Заведение одно — не спрашивай какое." if single_business else "- Если неясно какое заведение — уточни одним вопросом."}
- Услуга очевидна (одно совпадение) — переходи к шагу 2.
- Несколько похожих — уточни коротко: «Уточните: [вариант А] или [вариант Б]?»
- Не знает что выбрать — кратко опиши 2-3 варианта, спроси.

Шаг 2. Клиент называет КОГДА.
- Дата и время есть — сразу вызови get_available_slots.
- Только дата — вызови get_available_slots, покажи свободное время.
- «завтра», «в понедельник» — переведи в ISO сам.
- Нет даты — спроси: «На какое число?»
{"" if not has_venues else chr(10) + "Шаг 2а. Для аренды залов/комнат:" + chr(10) + "- Если есть несколько залов — уточни какой нужен, кратко опиши различия." + chr(10) + "- Если зал один — не спрашивай." + chr(10) + "- Предложи доп. услуги ПОСЛЕ выбора зала (если есть)."}

Шаг 3. Слот свободен — СРАЗУ вызови create_booking.
- Не переспрашивай «Вы уверены?» — бот сам покажет кнопку подтверждения.
- Слот занят — предложи 2-3 ближайших свободных.
- Нет слотов на дату — предложи ближайшие даты.

=== ДОПОЛНИТЕЛЬНЫЕ ДЕЙСТВИЯ ===
- «Мои записи» → get_my_bookings()
- «Отменить запись» → cancel_my_booking(booking_id)
- «Перенести» → reschedule_my_booking(booking_id, new_date, new_time)
- «Оплатил/перевёл» → confirm_payment_sent(booking_id)
- Вопрос об адресе/телефоне/часах работы — ответь из данных.

=== СТИЛЬ ОТВЕТОВ ===
- Кратко. 1-3 предложения. Без воды и предисловий.
- Дружелюбно, но по делу.
- Если вся информация есть — действуй, не переспрашивай.
- На вопросы не по теме: «Я помогаю с записью. Что хотите забронировать?»
- create_booking ОБЯЗАТЕЛЬНО с: business_id, service_id, date (YYYY-MM-DD), time (HH:MM)."""

    return prompt


# ──────────────────────────────────────────────────────────────────────────────
# Пример использования:
# ──────────────────────────────────────────────────────────────────────────────
#
# businesses = [
#     {
#         "id": 1,
#         "name": "Шиномонтаж (пример)",
#         "address": "Сочи, ул. Санаторная 74",
#         "phone": "89023911399",
#         "working_hours": "08:00-21:00",
#         "break_info": "13:00-14:00",
#         "business_type": "tire_service",
#         "services": [
#             {"id": 5, "name": "Шиномонтаж 16'-18'", "price": 1700, "duration": 20},
#             {"id": 6, "name": "Шиномонтаж 19'-22'", "price": 2500, "duration": 25},
#             {"id": 7, "name": "Балансировка", "price": 500, "duration": 15},
#             {"id": 8, "name": "Помывка шин", "price": 600, "duration": 15},
#         ],
#         "employees": [
#             {"id": 1, "name": "Максим", "role": "master"},
#             {"id": 2, "name": "Данил", "role": "master"},
#         ],
#     },
#     {
#         "id": 6,
#         "name": "Студия Город.А",
#         "address": "г.Сочи, ул.Донская, 3 «Б»",
#         "phone": "+79136292523",
#         "working_hours": "09:00-02:00",
#         "business_type": "venue_rental",
#         "prepayment_enabled": True,
#         "prepayment_percent": 100,
#         "payment_phone": "+79136292523",
#         "payment_details": "Тбанк (Кристина К.)",
#         "services": [
#             {"id": 21, "name": "1 час", "price": 600, "duration": 60},
#             {"id": 22, "name": "2 часа", "price": None, "duration": 120},
#             {"id": 23, "name": "3 часа", "price": None, "duration": 180},
#             {"id": 20, "name": "4 часа", "price": None, "duration": 240},
#             {"id": 29, "name": "Аренда тарелок для барабанной установки", "price": 300,
#              "is_additional": True},
#             {"id": 30, "name": "Аренда фортепиано", "price": 250,
#              "is_additional": True, "is_hourly_price": True},
#         ],
#         "employees": [
#             {"id": 18, "name": "Красная комната", "role": "master",
#              "capacity": 10, "specialization": "Для групп до 10 чел.",
#              "description": "Барабаны, гитарные стеки, пианино",
#              "time_pricing": [
#                  {"time_from": "09:00", "time_to": "00:00", "price_per_hour": 700},
#                  {"time_from": "00:00", "time_to": "09:00", "price_per_hour": 1000},
#              ]},
#             {"id": 19, "name": "Серая комната", "role": "master",
#              "capacity": 6, "solo_price_per_hour": 500},
#         ],
#     },
# ]
#
# prompt = build_client_system_prompt(businesses, [])
# print(prompt)
