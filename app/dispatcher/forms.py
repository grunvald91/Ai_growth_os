"""Project registered parameter schemas into bounded UI forms.

Model supplies a capability and suggested values, never controls or executable code.
Submissions are resolved against the persisted form, then use the existing dispatcher policy.
"""
from __future__ import annotations

from app.dispatcher.registry import CAPABILITIES
from app.dispatcher.policy import Cost
from app.tools import ToolOperation

# Audited individually. ANALYTICS alone is insufficient: some tools save settings.
READ_FORMS = frozenset({"list_tasks", "list_publications", "show_schedule", "show_insights"})
PREPARE_FORMS = frozenset({"create_post", "create_package", "whiteboard_video"})
VIDEO_EDIT_FIELDS = frozenset({"topic", "length", "call_to_action", "profile", "image_model", "visual_review", "science_mode"})
FORM_CAPABILITIES = READ_FORMS | PREPARE_FORMS
FIELD_LABELS = {
    "tab": "Статус задания", "kind": "Тип задания", "query": "Название или тема",
    "channel": "Площадка", "status": "Статус публикации", "view": "Что показать",
    "period": "Период", "topic": "Раздел аналитики", "days": "Количество дней",
    "presentation": "Представление данных",
    "audience": "Аудитория", "editorial_brief": "Дополнительные требования",
    "image_model": "Модель изображения", "image_ratio": "Пропорции изображения",
    "length": "Длительность", "call_to_action": "Призыв в конце", "profile": "Профиль моделей",
    "visual_review": "Проверка рисунков", "science_mode": "Режим содержания",
    "max_api_calls": "Лимит вызовов ИИ", "recovery_budget": "Дополнительный резерв вызовов",
    "source_script": "Исходный сценарий", "background_notes": "Дополнительный контекст",
    "reference_materials": "Справочные материалы",
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
    "dzen": "Дзен", "vc_ru": "vc.ru", "tenchat": "TenChat",
    "none": "Без изображения", "auto": "Автоматически", "gemini": "Gemini", "openai": "GPT Image",
    "4:5": "4:5", "1:1": "1:1", "table": "Таблица", "chart": "График", "trend": "Динамика записей",
    "30": "30 секунд", "60": "60 секунд", "configured": "Настроенный профиль",
    "gemini_ru": "Gemini (русская озвучка)", "gemini-3.1-flash-image": "Gemini",
    "gpt-image-2.5-sunburst": "GPT Image", "general": "Общий", "scientific": "Научный",
}


def form_for(capability_id: str | None, params: dict, *, partial: bool = True) -> dict:
    """Fail closed for unsupported shapes and invalid model-proposed values."""
    from app.dispatcher import executors
    cap = CAPABILITIES.get(capability_id)
    from app.dispatcher.policy import Verdict, verdict
    is_read = (cap is not None and cap.id in READ_FORMS and cap.operation == ToolOperation.ANALYTICS
               and cap.cost == Cost.NONE and not cap.always_confirm
               and verdict(cap.operation, cap.cost) == Verdict.RUN and cap.id in executors.EXECUTORS)
    is_prepare = (cap is not None and cap.id in PREPARE_FORMS and cap.operation == ToolOperation.GENERATE_DRAFT
                  and verdict(cap.operation, cap.cost) != Verdict.REFUSE
                  and cap.id in executors.CONFIRMERS
                  and ((cap.always_confirm and cap.id in executors.DESCRIBERS)
                       or (cap.id == "whiteboard_video" and cap.cost == Cost.PAID_LATER
                           and cap.id in executors.EXECUTORS)))
    if not (is_read or is_prepare):
        raise ValueError("Для этого действия интерактивная форма пока недоступна.")
    schema = cap.params.model_json_schema()
    required = set(schema.get("required", []))
    missing = required - params.keys()
    # Only the known required topic may be initially blank. Validate everything
    # else using the original cross-field validators, not a second schema.
    validation_params = dict(params)
    if partial and is_prepare and missing == {"topic"}:
        validation_params["topic"] = "Временная тема"
    initial = cap.params.model_validate(validation_params).model_dump(mode="json")
    if partial and is_prepare and missing == {"topic"}:
        initial["topic"] = ""
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
        field = {"name": name, "label": ("Тема материала" if name == "topic" and is_prepare else FIELD_LABELS[name]), "required": name in required,
                 "value": "" if value is None else str(value).lower() if isinstance(value, bool) else str(value)}
        if shape.get("type") == "string" and "enum" in shape:
            if optional or not all(v in OPTION_LABELS for v in shape["enum"]):
                raise ValueError("Не поддерживается вариант выбора.")
            field.update(kind="select", options=[
                {"value": v, "label": OPTION_LABELS[v]} for v in shape["enum"]])
        elif shape.get("type") == "boolean":
            field.update(kind="select", options=[{"value": "false", "label": "Нет"},
                                                  {"value": "true", "label": "Да"}])
        elif shape.get("type") == "integer" and "enum" in shape and optional:
            field.update(kind="select", options=[{"value": "", "label": "Автоматический минимум"}] +
                         [{"value": str(v), "label": str(v)} for v in shape["enum"]])
        elif shape.get("type") == "string" and "maxLength" in shape:
            field.update(kind="text", max_length=shape["maxLength"],
                         min_length=shape.get("minLength", 0))
        elif shape.get("type") == "integer" and "minimum" in shape and "maximum" in shape:
            field.update(kind="integer", minimum=shape["minimum"], maximum=shape["maximum"])
        else:
            raise ValueError("Не поддерживается форма параметра.")
        fields.append(field)
    if capability_id == "whiteboard_video":
        order = ("topic", "length", "image_model", "call_to_action", "profile", "science_mode",
                 "visual_review", "source_script", "background_notes", "reference_materials",
                 "max_api_calls", "recovery_budget")
        fields.sort(key=lambda field: order.index(field["name"]))
    if not fields:
        raise ValueError("У этого действия нет параметров для выбора.")
    return {"type": "tool_form", "capability": cap.id, "title": cap.title,
            "fields": fields, "submit_label": "Подготовить подтверждение" if is_prepare else "Показать результат"}
