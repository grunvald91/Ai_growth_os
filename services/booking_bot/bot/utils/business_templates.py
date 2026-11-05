"""
Шаблоны для быстрого создания бизнесов
"""
from datetime import time


# Шаблоны с предустановленными параметрами для разных типов бизнеса
BUSINESS_TEMPLATES = {
    "salon": {
        "name": "Салон красоты",
        "working_hours_start": time(9, 0),
        "working_hours_end": time(20, 0),
        "break_start": time(13, 0),
        "break_end": time(14, 0),
        "booking_interval_minutes": 30,
        "description": "💇 Профессиональные услуги красоты"
    },
    "psychologist": {
        "name": "Кабинет психолога",
        "working_hours_start": time(10, 0),
        "working_hours_end": time(19, 0),
        "break_start": None,
        "break_end": None,
        "booking_interval_minutes": 60,
        "description": "🧠 Психологические консультации"
    },
    "auto_service": {
        "name": "Автосервис",
        "working_hours_start": time(8, 0),
        "working_hours_end": time(18, 0),
        "break_start": time(12, 0),
        "break_end": time(13, 0),
        "booking_interval_minutes": 60,
        "description": "🚗 Ремонт и обслуживание автомобилей"
    },
    "tire_service": {
        "name": "Шиномонтаж",
        "working_hours_start": time(8, 0),
        "working_hours_end": time(18, 0),
        "break_start": time(12, 0),
        "break_end": time(13, 0),
        "booking_interval_minutes": 30,
        "description": "🔧 Шиномонтаж и балансировка"
    },
    "spa": {
        "name": "СПА-салон",
        "working_hours_start": time(10, 0),
        "working_hours_end": time(21, 0),
        "break_start": time(14, 0),
        "break_end": time(15, 0),
        "booking_interval_minutes": 60,
        "description": "💆 Массаж и СПА-процедуры"
    },
    "medical": {
        "name": "Медицинская клиника",
        "working_hours_start": time(8, 0),
        "working_hours_end": time(20, 0),
        "break_start": time(12, 0),
        "break_end": time(13, 0),
        "booking_interval_minutes": 30,
        "description": "🏥 Медицинские услуги"
    },
    "education": {
        "name": "Учебный центр",
        "working_hours_start": time(9, 0),
        "working_hours_end": time(18, 0),
        "break_start": time(12, 0),
        "break_end": time(13, 0),
        "booking_interval_minutes": 60,
        "description": "🎓 Обучающие курсы и занятия"
    },
    "venue_rental": {
        "name": "Аренда помещений",
        "working_hours_start": time(8, 0),
        "working_hours_end": time(23, 0),
        "break_start": None,
        "break_end": None,
        "booking_interval_minutes": 60,  # Шаг 1 час для аренды
        "description": "🏠 Аренда залов и помещений",
        "default_services": [  # Предустановленные услуги-время
            {"name": "1 час", "duration_minutes": 60, "price": None},
            {"name": "2 часа", "duration_minutes": 120, "price": None},
            {"name": "3 часа", "duration_minutes": 180, "price": None},
        ],
        "default_additional_services": []
    },
    "other": {
        "name": "Бизнес",
        "working_hours_start": time(9, 0),
        "working_hours_end": time(18, 0),
        "break_start": None,
        "break_end": None,
        "booking_interval_minutes": 30,
        "description": ""
    }
}


def get_template(business_type: str) -> dict:
    """Получить шаблон для типа бизнеса"""
    return BUSINESS_TEMPLATES.get(business_type, BUSINESS_TEMPLATES["other"])
