"""
Tool definitions for AI admin.
TOOL_SCHEMAS  — список для OpenRouter function calling.
READ_ONLY_TOOLS — выполняются сразу без подтверждения.
MEDIA_TOOLS   — file_id берётся из message, не из args.
describe_tool_call() — описание для карточки подтверждения.

Примечание: параметр business_id опционален — используется когда у владельца
несколько бизнесов. Для единственного бизнеса AI его не указывает.
"""

# Опциональный параметр business_id добавляется в каждый инструмент
# (нужен когда у владельца несколько бизнесов)
_BIZ_ID_PROP = {"business_id": {"type": "integer", "description": "ID бизнеса (обязательно если у владельца несколько бизнесов)"}}

TOOL_SCHEMAS = [
    # ── НАСТРОЙКИ ──────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "update_name", "description": "Изменить название бизнеса",
        "parameters": {"type": "object",
                       "properties": {"new_name": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["new_name"]}}},

    {"type": "function", "function": {
        "name": "update_description", "description": "Изменить описание бизнеса",
        "parameters": {"type": "object",
                       "properties": {"new_description": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["new_description"]}}},

    {"type": "function", "function": {
        "name": "update_address", "description": "Изменить адрес",
        "parameters": {"type": "object",
                       "properties": {"new_address": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["new_address"]}}},

    {"type": "function", "function": {
        "name": "update_phone", "description": "Изменить номер телефона",
        "parameters": {"type": "object",
                       "properties": {"new_phone": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["new_phone"]}}},

    {"type": "function", "function": {
        "name": "update_working_hours", "description": "Изменить часы работы",
        "parameters": {"type": "object",
                       "properties": {"start": {"type": "string", "description": "HH:MM"},
                                      "end":   {"type": "string", "description": "HH:MM"},
                                      **_BIZ_ID_PROP},
                       "required": ["start", "end"]}}},

    {"type": "function", "function": {
        "name": "update_break_time",
        "description": "Изменить перерыв. Для удаления передай пустые строки.",
        "parameters": {"type": "object",
                       "properties": {"start": {"type": "string", "description": "HH:MM или пустая строка"},
                                      "end":   {"type": "string", "description": "HH:MM или пустая строка"},
                                      **_BIZ_ID_PROP},
                       "required": ["start", "end"]}}},

    {"type": "function", "function": {
        "name": "update_directions", "description": "Изменить схему проезда / как добраться",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["text"]}}},

    {"type": "function", "function": {
        "name": "delete_all_photos", "description": "Удалить все фотографии из галереи",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "add_photo", "description": "Добавить фото (из прикреплённого файла) в галерею бизнеса",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "update_video",
        "description": "Обновить видео бизнеса (из прикреплённого видеофайла)",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "update_cancellation_photo",
        "description": "Обновить фото политики отмены (из прикреплённого фото)",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "toggle_prepayment", "description": "Включить или выключить предоплату",
        "parameters": {"type": "object",
                       "properties": {"enabled": {"type": "boolean"}, **_BIZ_ID_PROP},
                       "required": ["enabled"]}}},

    {"type": "function", "function": {
        "name": "update_prepayment_settings",
        "description": "Изменить процент, телефон или реквизиты предоплаты",
        "parameters": {"type": "object",
                       "properties": {"percent": {"type": "integer", "minimum": 1, "maximum": 100},
                                      "phone":   {"type": "string"},
                                      "details": {"type": "string"},
                                      **_BIZ_ID_PROP},
                       "required": ["percent"]}}},

    {"type": "function", "function": {
        "name": "update_ai_instruction",
        "description": "Обновить инструкцию AI-администратора для этого бизнеса",
        "parameters": {"type": "object",
                       "properties": {"instruction": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["instruction"]}}},

    # ── READ-ONLY ───────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "get_current_settings",
        "description": "Показать все текущие настройки бизнеса",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "get_business_services",
        "description": "Получить список услуг и сотрудников бизнеса (нужно перед созданием записи для клиента)",
        "parameters": {"type": "object", "properties": {**_BIZ_ID_PROP}}}},

    # ── ЗАПИСИ ─────────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "get_bookings",
        "description": "Получить список записей. Все параметры опциональны.",
        "parameters": {"type": "object",
                       "properties": {
                           "date":        {"type": "string", "description": "YYYY-MM-DD"},
                           "status":      {"type": "string",
                                           "enum": ["pending", "confirmed", "cancelled", "completed", "all"]},
                           "client_name": {"type": "string"},
                           **_BIZ_ID_PROP}}}},

    {"type": "function", "function": {
        "name": "cancel_booking", "description": "Отменить запись клиента",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"},
                                      "reason":     {"type": "string"},
                                      **_BIZ_ID_PROP},
                       "required": ["booking_id"]}}},

    {"type": "function", "function": {
        "name": "reschedule_booking", "description": "Перенести запись на другую дату и время",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"},
                                      "new_date":   {"type": "string", "description": "YYYY-MM-DD"},
                                      "new_time":   {"type": "string", "description": "HH:MM"},
                                      **_BIZ_ID_PROP},
                       "required": ["booking_id", "new_date", "new_time"]}}},

    {"type": "function", "function": {
        "name": "add_booking_note", "description": "Добавить заметку к записи",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"},
                                      "note":       {"type": "string"},
                                      **_BIZ_ID_PROP},
                       "required": ["booking_id", "note"]}}},

    {"type": "function", "function": {
        "name": "confirm_booking", "description": "Подтвердить запись (pending → confirmed)",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"}, **_BIZ_ID_PROP},
                       "required": ["booking_id"]}}},

    {"type": "function", "function": {
        "name": "create_booking_for_client",
        "description": "Создать запись для клиента от имени владельца/администратора",
        "parameters": {"type": "object",
                       "properties": {
                           "service_id":   {"type": "integer", "description": "ID услуги (из get_business_services)"},
                           "employee_id":  {"type": "integer", "description": "ID сотрудника (из get_business_services), опционально"},
                           "date":         {"type": "string", "description": "YYYY-MM-DD"},
                           "time":         {"type": "string", "description": "HH:MM"},
                           "client_name":  {"type": "string", "description": "Имя клиента"},
                           "client_phone": {"type": "string", "description": "Телефон клиента"},
                           "notes":        {"type": "string", "description": "Заметка к записи"},
                           **_BIZ_ID_PROP},
                       "required": ["service_id", "date", "time", "client_name"]}}},

    # ── ОПОВЕЩЕНИЯ ──────────────────────────────────────────────────────────
    {"type": "function", "function": {
        "name": "send_message_to_client",
        "description": "Отправить личное сообщение клиенту по ID записи",
        "parameters": {"type": "object",
                       "properties": {"booking_id": {"type": "integer"},
                                      "text":       {"type": "string"},
                                      **_BIZ_ID_PROP},
                       "required": ["booking_id", "text"]}}},

    {"type": "function", "function": {
        "name": "send_broadcast",
        "description": "Разослать сообщение всем клиентам бизнеса",
        "parameters": {"type": "object",
                       "properties": {"text": {"type": "string"}, **_BIZ_ID_PROP},
                       "required": ["text"]}}},
]

READ_ONLY_TOOLS = {"get_bookings", "get_current_settings", "get_business_services"}
MEDIA_TOOLS     = {"add_photo", "update_video", "update_cancellation_photo"}


def describe_tool_call(name: str, args: dict) -> str:
    """Human-readable description for confirmation card."""
    biz_note = f" (бизнес #{args['business_id']})" if args.get('business_id') else ""
    m = {
        "update_name":               f"Изменить название на:\n«{args.get('new_name')}»{biz_note}",
        "update_description":        f"Изменить описание на:\n{str(args.get('new_description', ''))[:300]}{biz_note}",
        "update_address":            f"Изменить адрес на:\n{args.get('new_address')}{biz_note}",
        "update_phone":              f"Изменить телефон на:\n{args.get('new_phone')}{biz_note}",
        "update_working_hours":      f"Изменить часы работы:\n{args.get('start')} – {args.get('end')}{biz_note}",
        "update_break_time":         f"Изменить перерыв:\n{args.get('start') or 'убрать'} – {args.get('end') or ''}{biz_note}",
        "update_directions":         f"Изменить схему проезда:\n{str(args.get('text', ''))[:300]}{biz_note}",
        "delete_all_photos":         f"Удалить ВСЕ фотографии из галереи{biz_note}",
        "add_photo":                 f"Добавить прикреплённое фото в галерею{biz_note}",
        "update_video":              f"Обновить видео бизнеса{biz_note}",
        "update_cancellation_photo": f"Обновить фото политики отмены{biz_note}",
        "toggle_prepayment":         f"{'Включить' if args.get('enabled') else 'Выключить'} предоплату{biz_note}",
        "update_prepayment_settings":f"Обновить предоплату: {args.get('percent')}%"
                                     + (f", тел: {args.get('phone')}" if args.get("phone") else "")
                                     + biz_note,
        "update_ai_instruction":     f"Обновить инструкцию AI:\n{str(args.get('instruction', ''))[:200]}…{biz_note}",
        "cancel_booking":            f"Отменить запись #{args.get('booking_id')}"
                                     + (f"\nПричина: {args.get('reason')}" if args.get("reason") else "")
                                     + biz_note,
        "reschedule_booking":        f"Перенести запись #{args.get('booking_id')} на\n"
                                     f"{args.get('new_date')} в {args.get('new_time')}{biz_note}",
        "add_booking_note":          f"Заметка к записи #{args.get('booking_id')}:\n{args.get('note')}{biz_note}",
        "confirm_booking":           f"Подтвердить запись #{args.get('booking_id')}{biz_note}",
        "send_message_to_client":    f"Сообщение клиенту (запись #{args.get('booking_id')}):\n{args.get('text')}{biz_note}",
        "send_broadcast":            f"Рассылка ВСЕМ клиентам:\n{args.get('text')}{biz_note}",
        "create_booking_for_client": (
            f"Записать клиента{biz_note}:\n"
            f"👤 {args.get('client_name')} {args.get('client_phone') or ''}\n"
            f"📅 {args.get('date')} в {args.get('time')}\n"
            f"🔧 Услуга #{args.get('service_id')}"
            + (f"\n👥 Сотрудник #{args.get('employee_id')}" if args.get('employee_id') else "")
            + (f"\n📝 {args.get('notes')}" if args.get('notes') else "")
        ),
    }
    return m.get(name, f"Выполнить: {name}")
