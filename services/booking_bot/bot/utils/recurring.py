"""Чистые хелперы для повторяющихся (еженедельных) записей — без обращения к БД."""
from datetime import date, datetime, time, timedelta
from typing import List, Tuple


def compute_recurring_dates(start: date, weeks: int) -> List[date]:
    """Вернуть `weeks` дат начиная с `start`, с шагом в неделю.

    `weeks=1` — вернёт только `[start]` (без повтора, дефолтный случай,
    когда `repeat_weeks` не задан). Диапазон 2-8 — то, что видит
    пользователь в UI; 1 используется внутри хендлеров как "без повтора".
    """
    if weeks < 1 or weeks > 8:
        raise ValueError(f"weeks must be between 1 and 8, got {weeks}")
    return [start + timedelta(weeks=i) for i in range(weeks)]


def build_visit_slots(services: List[dict], start_time: time) -> List[Tuple[int, time]]:
    """Построить список (service_id, время начала) для визита с несколькими
    услугами подряд — каждая следующая начинается сразу после окончания
    предыдущей (та же логика, что уже используют admin/master-хендлеры).

    `services` — список словарей с ключами `id` и `duration` (минуты).
    Дата для промежуточных вычислений не важна — используется только
    итоговое время (`.time()`), не дата.
    """
    slots = []
    current_time = start_time
    anchor_date = date(2000, 1, 1)
    for service in services:
        slots.append((service['id'], current_time))
        current_datetime = datetime.combine(anchor_date, current_time) + timedelta(minutes=service['duration'])
        current_time = current_datetime.time()
    return slots
