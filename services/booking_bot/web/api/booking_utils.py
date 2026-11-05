"""Чистые хелперы для повторяющихся (еженедельных) записей — без обращения к БД."""
from datetime import date, timedelta
from typing import List


def compute_recurring_dates(start: date, weeks: int) -> List[date]:
    """Вернуть `weeks` дат начиная с `start`, с шагом в неделю.

    `weeks=1` — вернёт только `[start]` (без повтора, это дефолтный случай,
    когда `repeat_weeks` не задан клиентом). Диапазон 2-8 — то, что видит
    пользователь в UI; 1 используется внутри роутов как "без повтора".
    """
    if weeks < 1 or weeks > 8:
        raise ValueError(f"weeks must be between 1 and 8, got {weeks}")
    return [start + timedelta(weeks=i) for i in range(weeks)]
