"""
Tool definitions for client-side AI admin.
Clients can browse businesses/services, check slots, book, view and manage their bookings.
"""

CLIENT_TOOL_SCHEMAS = [
    # ── READ-ONLY ───────────────────────────────────────────────────────────

    {"type": "function", "function": {
        "name": "get_additional_services",
        "description": "Показать дополнительные услуги заведения (только для venue_rental). Вызывай после выбора основной услуги.",
        "parameters": {"type": "object",
                       "properties": {"business_id": {"type": "integer"}},
                       "required": ["business_id"]}}},

    {"type": "function", "function": {
        "name": "get_available_slots",
        "description": "Получить доступные слоты для записи на дату",
        "parameters": {"type": "object",
                       "properties": {
                           "business_id":  {"type": "integer"},
                           "service_id":   {"type": "integer"},
                           "date":         {"type": "string", "description": "YYYY-MM-DD"},
                           "employee_id":  {"type": "integer", "description": "Опционально: конкретный сотрудник/зал"}},
                       "required": ["business_id", "service_id", "date"]}}},

    {"type": "function", "function": {
        "name": "get_my_bookings",
        "description": "Показать записи клиента",
        "parameters": {"type": "object",
                       "properties": {
                           "status": {"type": "string",
                                      "enum": ["upcoming", "all", "cancelled"],
                                      "description": "Фильтр статуса. По умолчанию upcoming."}}}}},

    # ── ТРЕБУЮТ ПОДТВЕРЖДЕНИЯ ──────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "create_booking",
        "description": "Создать запись клиента. После создания бот сам уведомит об условиях оплаты если требуется предоплата.",
        "parameters": {"type": "object",
                       "properties": {
                           "business_id":           {"type": "integer"},
                           "service_id":            {"type": "integer"},
                           "date":                  {"type": "string", "description": "YYYY-MM-DD"},
                           "time":                  {"type": "string", "description": "HH:MM"},
                           "employee_id":           {"type": "integer"},
                           "additional_service_ids":{"type": "array", "items": {"type": "integer"},
                                                     "description": "ID дополнительных услуг (опционально)"},
                           "notes":                 {"type": "string"},
                           "business_name":         {"type": "string", "description": "Название заведения (для подтверждения)"},
                           "service_name":          {"type": "string", "description": "Название услуги (для подтверждения)"}},
                       "required": ["business_id", "service_id", "date", "time"]}}},

    {"type": "function", "function": {
        "name": "confirm_payment_sent",
        "description": "Клиент подтверждает что отправил предоплату. Уведомляет владельца для проверки.",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"}},
                       "required": ["booking_id"]}}},

    {"type": "function", "function": {
        "name": "cancel_my_booking",
        "description": "Отменить свою запись",
        "parameters": {"type": "object",
                       "properties": {
                           "booking_id": {"type": "integer"},
                           "reason":     {"type": "string"}},
                       "required": ["booking_id"]}}},

    {"type": "function", "function": {
        "name": "reschedule_my_booking",
        "description": "Перенести свою запись на другую дату и время",
        "parameters": {"type": "object",
                       "properties": {
                           "booking_id": {"type": "integer"},
                           "new_date":   {"type": "string", "description": "YYYY-MM-DD"},
                           "new_time":   {"type": "string", "description": "HH:MM"}},
                       "required": ["booking_id", "new_date", "new_time"]}}},
]

CLIENT_READ_ONLY_TOOLS = {
    "get_additional_services",
    "get_available_slots", "get_my_bookings",
}


def describe_client_tool_call(name: str, args: dict) -> str:
    """Human-readable description for client confirmation card."""
    m = {
        "create_booking": (
            f"Записать вас:\n"
            f"📍 {args.get('business_name') or ('Заведение #' + str(args.get('business_id')))}\n"
            f"💆 {args.get('service_name') or ('Услуга #' + str(args.get('service_id')))}\n"
            f"📅 {args.get('date')} в {args.get('time')}"
            + (f"\n🛎 Доп. услуги: {args.get('additional_service_ids')}" if args.get("additional_service_ids") else "")
            + (f"\n💬 {args.get('notes')}" if args.get("notes") else "")
        ),
        "confirm_payment_sent": f"Подтвердить отправку предоплаты по записи #{args.get('booking_id')}",
        "cancel_my_booking": (
            f"Отменить запись #{args.get('booking_id')}"
            + (f"\nПричина: {args.get('reason')}" if args.get("reason") else "")
        ),
        "reschedule_my_booking": (
            f"Перенести запись #{args.get('booking_id')} на\n"
            f"{args.get('new_date')} в {args.get('new_time')}"
        ),
    }
    return m.get(name, f"Выполнить: {name}")
