"""Pure notification timing rules shared by API and scheduler."""
from __future__ import annotations

import json
from collections.abc import Iterable


DEFAULT_STAFF_REMINDER_MINUTES = (1440, 120)
MIN_REMINDER_MINUTES = 5
MAX_REMINDER_MINUTES = 10080
MAX_REMINDERS_PER_AUDIENCE = 6


def normalize_reminder_minutes(
    value,
    *,
    fallback: Iterable[int] | None = DEFAULT_STAFF_REMINDER_MINUTES,
) -> list[int]:
    if value is None:
        return list(fallback or ())

    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return list(fallback or ())

    if not isinstance(value, (list, tuple)):
        return list(fallback or ())

    result: list[int] = []
    for item in value:
        try:
            minutes = int(item)
        except (TypeError, ValueError):
            continue
        if (
            MIN_REMINDER_MINUTES <= minutes <= MAX_REMINDER_MINUTES
            and minutes not in result
        ):
            result.append(minutes)

    result.sort(reverse=True)
    result = result[:MAX_REMINDERS_PER_AUDIENCE]
    if result:
        return result
    return list(fallback or ())


def due_reminder_offset(
    remaining_minutes: float,
    offsets: Iterable[int],
) -> int | None:
    """Return only the nearest configured threshold already reached.

    Example: for [1440, 120, 30] and 90 minutes remaining, return 120.
    This prevents a scheduler that starts late from sending all missed
    reminders at once.
    """
    if remaining_minutes <= 0:
        return None
    eligible = [int(offset) for offset in offsets if remaining_minutes <= int(offset)]
    return min(eligible) if eligible else None


def format_reminder_offset(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440} дн."
    if minutes % 60 == 0:
        return f"{minutes // 60} ч."
    return f"{minutes} мин."
