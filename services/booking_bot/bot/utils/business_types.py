"""
Типы бизнесов и их отображение
"""

BUSINESS_TYPES = {
    "salon": "Салон красоты",
    "psychologist": "Психолог",
    "auto_service": "Автосервис",
    "tire_service": "Шиномонтаж",
    "spa": "Массаж/СПА",
    "medical": "Медицинская клиника",
    "education": "Обучение",
    "venue_rental": "Аренда помещений",  # Сауны, студии и т.п.
    "other": "Другое",
}


# Типы бизнесов, где используется аренда помещений (помещения вместо мастеров, время вместо услуг)
VENUE_RENTAL_TYPES = ["venue_rental"]


def is_venue_rental_type(business_type: str) -> bool:
    """Проверить, является ли тип бизнеса арендой помещений"""
    return business_type in VENUE_RENTAL_TYPES


# Терминология для разных типов бизнеса
BUSINESS_TERMINOLOGY = {
    "default": {
        "employee": "Мастер",
        "employees": "Мастера",
        "employee_acc": "мастера",  # винительный падеж
        "service": "Услуга",
        "services": "Услуги",
        "service_acc": "услугу",
    },
    "venue_rental": {
        "employee": "Зал",
        "employees": "Залы",
        "employee_acc": "зал",
        "service": "Время",
        "services": "Время",
        "service_acc": "время",
    },
}


def get_terminology(business_type: str, key: str = None):
    """
    Получить термин для типа бизнеса.

    Если key указан - возвращает строку с конкретным термином.
    Если key не указан - возвращает словарь со всеми терминами для типа бизнеса.
    """
    if business_type in BUSINESS_TERMINOLOGY:
        terms = BUSINESS_TERMINOLOGY[business_type].copy()
    else:
        terms = BUSINESS_TERMINOLOGY["default"].copy()

    # Добавляем дополнительные ключи для совместимости
    terms["is_venue"] = business_type in VENUE_RENTAL_TYPES
    terms["employee_singular"] = terms.get("employee_acc", "мастера")
    terms["employee_plural"] = terms.get("employees", "мастеров").lower()

    if key is None:
        return terms
    return terms.get(key, BUSINESS_TERMINOLOGY["default"].get(key, key))


def get_business_type_name(type_code: str) -> str:
    """Получить читаемое название типа бизнеса"""
    return BUSINESS_TYPES.get(type_code, type_code)
