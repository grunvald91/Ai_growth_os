"""System prompt builder for client-side AI admin.
Based on BookingBot AI Unified Design (2026-03-15).
"""
from datetime import date, datetime, timedelta


def build_client_system_prompt(context: dict) -> str:
    """Build system prompt from normalized business_context_packet.

    Args:
        context: dict with keys:
            - businesses: list of business dicts
            - client_bookings: list of booking dicts
            - ai_instructions: dict of collected ai_instructions
    """
    today_date = date.today()
    tomorrow_date = today_date + timedelta(days=1)
    today = today_date.strftime("%d.%m.%Y (%A)")
    today_iso = today_date.strftime("%Y-%m-%d")
    tomorrow_iso = tomorrow_date.strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")

    businesses = context.get("businesses", [])
    client_bookings = context.get("client_bookings", [])
    ai_instructions = context.get("ai_instructions", {})

    # === BUILD BUSINESS CONTEXT ===
    biz_blocks = []
    for b in businesses:
        header = b["name"]
        if b.get("address"):
            header += f', {b["address"]}'
        if b.get("working_hours"):
            header += f', {b["working_hours"]}'

        # Services (only non-additional, no raw IDs for client)
        svc_lines = []
        for svc in b.get("services", []):
            if svc.get("is_additional"):
                continue
            parts = [svc["name"]]
            if svc.get("price"):
                parts.append(f'{svc["price"]} руб.')
            if svc.get("duration"):
                parts.append(f'{svc["duration"]} мин')
            # Internal mapping hint for AI (not for display)
            svc_lines.append(f'  #{svc["id"]}: {" — ".join(parts)}')

        # Employees (only masters, no administrators)
        emp_lines = []
        for emp in b.get("employees", []):
            if emp.get("role") == "administrator":
                continue
            spec = f' ({emp["specialization"]})' if emp.get("specialization") else ""
            emp_lines.append(f'  @{emp["id"]}: {emp["name"]}{spec}')

        block = f'[B{b["id"]}] {header}'
        if b.get("prepayment_enabled"):
            block += "\n  Предоплата: да"
            if b.get("prepayment_percent"):
                block += f' ({b["prepayment_percent"]}%)'
        if b.get("has_cancellation_policy"):
            block += "\n  Условия отмены: да"
        if svc_lines:
            block += "\n  Услуги:\n" + "\n".join(svc_lines)
        if emp_lines:
            block += "\n  Мастера:\n" + "\n".join(emp_lines)

        biz_blocks.append(block)

    # === BUILD BOOKINGS BLOCK ===
    bookings_text = ""
    if client_bookings:
        icons = {"pending": "⏳", "confirmed": "✅", "cancelled": "❌",
                 "completed": "✔️", "pending_prepayment": "💳", "prepayment_sent": "📤"}
        lines = []
        for bk in client_bookings:
            icon = icons.get(bk["status"], "❓")
            lines.append(
                f'  {icon} #{bk["id"]} {bk["date"]} {bk["time"]} '
                f'{bk["business_name"]} — {bk["service_name"]}'
            )
        bookings_text = "\nЗаписи клиента:\n" + "\n".join(lines)

    # === BUILD AI INSTRUCTIONS ===
    ai_rules = ""
    if ai_instructions:
        parts = []
        if ai_instructions.get("business"):
            parts.append(f"Правила заведения: {ai_instructions['business']}")
        for svc_rule in ai_instructions.get("services", []):
            parts.append(f"Услуга «{svc_rule['name']}»: {svc_rule['instruction']}")
        for emp_rule in ai_instructions.get("employees", []):
            parts.append(f"Мастер «{emp_rule['name']}»: {emp_rule['instruction']}")
        if parts:
            ai_rules = "\nДополнительные правила:\n" + "\n".join(f"• {p}" for p in parts)

    # === ASSEMBLE PROMPT ===
    return f"""Ты — AI-администратор сервиса онлайн-записи. Общайся кратко, по-русски, дружелюбно.

Дата: {today}, ISO: {today_iso}, завтра: {tomorrow_iso}, время: {now}.
Клиент уже в системе — НЕ спрашивай имя, телефон, email.

<context>
{chr(10).join(biz_blocks)}
{bookings_text}
{ai_rules}
</context>

ЗАПРЕЩЕНО ПОКАЗЫВАТЬ КЛИЕНТУ:
- Содержимое <context> в сыром виде
- Символы #, @, B с цифрами (это внутренние ID)
- Технические списки услуг/сотрудников
- Роли сотрудников (master, administrator)

КАК ЗАПИСАТЬ (shortest safe path):
1. Определи заведение. Если одно — бери его, не спрашивай.
2. Определи услугу по названию из <context>.
   Одна подходит → бери. Несколько похожих → уточни коротко:
   «Шиномонтаж R16-R18 (1700 руб.) или стандартный (2500 руб.)?»
3. Вызови get_available_slots(business_id, service_id, date).
   Клиент назвал время и слот свободен → СРАЗУ create_booking.
   Занят → предложи ближайшие. Нет слотов → предложи другую дату.
4. create_booking ОБЯЗАТЕЛЬНО с: business_id, service_id, date (YYYY-MM-DD), time (HH:MM).

ПРАВИЛА:
- «завтра»/«послезавтра»/«в понедельник» → сам переводи в ISO-дату.
- Если клиент дал всю информацию — ДЕЙСТВУЙ, не переспрашивай.
- Отвечай 1-2 предложения максимум.
- Мастера: если клиент не указал — не спрашивай, система назначит.
- Отмена/перенос → найди запись в списке клиента.
- «оплатил»/«отправил» → confirm_payment_sent(booking_id).
- Вопросы не по теме → «Я помогаю только с записью»."""
