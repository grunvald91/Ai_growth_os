"""
Функции валидации входных данных и форматирования
"""
import re
from datetime import date, datetime

# Названия дней недели на русском
WEEKDAYS_RU = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
WEEKDAYS_FULL_RU = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']


def format_date_with_weekday(d: date | datetime, short: bool = False) -> str:
    """
    Форматирование даты с днём недели
    short=True: 15.01 (Пн)
    short=False: 15.01.2026 (Пн)
    """
    if isinstance(d, datetime):
        d = d.date()
    weekday = WEEKDAYS_RU[d.weekday()]
    if short:
        return f"{d.strftime('%d.%m')} ({weekday})"
    return f"{d.strftime('%d.%m.%Y')} ({weekday})"


def format_phone(phone: str) -> str:
    """
    Форматирование телефона: если 10 цифр - добавляет +7
    Возвращает телефон в формате +7XXXXXXXXXX или 8XXXXXXXXXX
    """
    if not phone:
        return phone

    # Убираем все кроме цифр и +
    clean = ''.join(c for c in phone if c.isdigit() or c == '+')

    # Убираем + для обработки
    if clean.startswith('+'):
        clean = clean[1:]

    # Если 10 цифр - добавляем +7
    if len(clean) == 10:
        return f"+7{clean}"

    # Если 11 цифр и начинается с 8 - оставляем как есть
    if len(clean) == 11 and clean.startswith('8'):
        return f"8{clean[1:]}"

    # Если 11 цифр и начинается с 7 - добавляем +
    if len(clean) == 11 and clean.startswith('7'):
        return f"+7{clean[1:]}"

    # Возвращаем как есть если не подходит
    return phone


def validate_phone(phone: str) -> bool:
    """Проверка формата номера телефона"""
    # Допустимые форматы: +7XXXXXXXXXX, 8XXXXXXXXXX, 7XXXXXXXXXX
    clean_phone = re.sub(r'[\s\-\(\)]', '', phone)

    if clean_phone.startswith('+'):
        clean_phone = clean_phone[1:]

    # Должно быть 10-11 цифр
    if not clean_phone.isdigit():
        return False

    if len(clean_phone) not in [10, 11]:
        return False

    # Если 11 цифр, должно начинаться с 7 или 8
    if len(clean_phone) == 11 and clean_phone[0] not in ['7', '8']:
        return False

    return True


def validate_duration(duration_str: str) -> tuple[bool, int]:
    """
    Проверка длительности услуги
    Возвращает (валидно, значение)
    """
    try:
        duration = int(duration_str)
        if duration <= 0 or duration > 480:  # Макс 8 часов
            return False, 0
        return True, duration
    except ValueError:
        return False, 0


def validate_price(price_str: str) -> tuple[bool, float]:
    """
    Проверка цены услуги
    Возвращает (валидно, значение)
    """
    try:
        price = float(price_str)
        if price < 0 or price > 1000000:  # Макс 1 млн
            return False, 0.0
        return True, price
    except ValueError:
        return False, 0.0


def safe_split_callback(callback_data: str, expected_parts: int = 2) -> list[str] | None:
    """
    Безопасный парсинг callback_data
    Возвращает список частей или None если формат неверный
    """
    parts = callback_data.split(":")
    if len(parts) != expected_parts:
        return None
    return parts
