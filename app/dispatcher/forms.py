"""Project registered parameter schemas into bounded, read-only UI forms.

Model supplies a capability and suggested values, never controls or executable code.
A form submission goes through the same validated dispatcher chip path.
"""
from __future__ import annotations

from app.dispatcher.registry import CAPABILITIES
from app.dispatcher.policy import Cost
from app.tools import ToolOperation

# Audited individually. ANALYTICS alone is insufficient: some tools save settings.
FORM_CAPABILITIES = frozenset({"list_tasks", "list_publications", "show_schedule", "show_insights"})
FIELD_LABELS = {
    "tab": "Статус задания", "kind": "Тип задания", "query": "Название или тема",
    "channel": "Площадка", "status": "Статус публикации", "view": "Что показать",
    "period": "Период", "topic": "Раздел аналитики", "days": "Количество дней",
}
OPTION_LABELS = {
    "all": "Все", "active": "В работе", "approval": "На проверке", "ready": "Готовые",
    "problems": "С проблемами", "post": "Пост", "article": "Статья", "creative": "Креатив",
    "video": "Видео", "plan": "План", "edit": "Редактирование", "seo": "SEO",
    "business": "Бизнес", "telegram": "Telegram", "vk": "ВКонтакте",
    "scheduled": "Запланировано", "sending": "Отправляется", "published": "Опубликовано",
    "cancelled": "Отменено", "bookings": "Записи", "free": "Свободные окна",
    "load": "Загрузка", "next": "Следующий клиент", "today": "Сегодня", "tomorrow": "Завтра",
    "week": "Ближайшие 7 дней", "cancellations": "Отмены", "money": "Расчётная стоимость записей",
    "clients": "Клиенты", "weekdays": "Дни недели", "daytime": "Время дня",
    "services": "Услуги", "upcoming": "Предстоящие записи",
}


def form_for(capability_id: str | None, params: dict) -> dict:
    """Fail closed for unsupported shapes and invalid model-proposed values."""
    from app.dispatcher import executors
    cap = CAPABILITIES.get(capability_id)
    if (cap is None or cap.id not in FORM_CAPABILITIES or cap.operation != ToolOperation.ANALYTICS
            or cap.cost != Cost.NONE or cap.always_confirm or cap.id not in executors.EXECUTORS):
        raise ValueError("Для этого действия интерактивная форма пока недоступна.")
    initial = cap.params.model_validate(params).model_dump(mode="json")
    schema = cap.params.model_json_schema()
    required = set(schema.get("required", []))
    fields = []
    for name, prop in schema.get("properties", {}).items():
        if name not in FIELD_LABELS:
            raise ValueError("Для этого поля пока нет безопасного представления.")
        shape = prop
        optional = False
        if "anyOf" in prop:
            variants = prop["anyOf"]
            non_null = [item for item in variants if item.get("type") != "null"]
            if len(variants) != 2 or len(non_null) != 1:
                raise ValueError("Не поддерживается форма параметра.")
            shape = non_null[0]
            optional = True
        value = initial.get(name)
        field = {"name": name, "label": FIELD_LABELS[name], "required": name in required,
                 "value": "" if value is None else str(value)}
        if shape.get("type") == "string" and "enum" in shape:
            if optional or not all(v in OPTION_LABELS for v in shape["enum"]):
                raise ValueError("Не поддерживается вариант выбора.")
            field.update(kind="select", options=[
                {"value": v, "label": OPTION_LABELS[v]} for v in shape["enum"]])
        elif shape.get("type") == "string" and "maxLength" in shape and not optional:
            field.update(kind="text", max_length=shape["maxLength"],
                         min_length=shape.get("minLength", 0))
        elif shape.get("type") == "integer" and "minimum" in shape and "maximum" in shape:
            field.update(kind="integer", minimum=shape["minimum"], maximum=shape["maximum"])
        else:
            raise ValueError("Не поддерживается форма параметра.")
        fields.append(field)
    if not fields:
        raise ValueError("У этого действия нет параметров для выбора.")
    return {"type": "tool_form", "capability": cap.id, "title": cap.title,
            "fields": fields, "submit_label": "Показать результат"}
