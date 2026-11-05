"""Pure pricing and interval math shared by rental endpoints."""
from datetime import date, datetime, time, timedelta


def price_quote(hourly_rate: int, hours: int, fixed_addons: list[int]) -> tuple[int | None, bool, int]:
    """Return (quoted total or None, manager confirmation needed, fixed addon subtotal)."""
    if hourly_rate <= 0 or hours < 1 or hours > 12:
        raise ValueError('invalid rental rate or duration')
    addons = sum(fixed_addons)
    if hours < 5:
        return None, True, addons
    return hourly_rate * hours + addons, False, addons


def intervals_overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def available_starts(day: date, opens: time, closes: time, duration: int,
                     busy: list[tuple[time | int, int]], now: datetime | None = None,
                     *, interval: int = 60, break_start: time | None = None,
                     break_end: time | None = None) -> list[str]:
    """Return configured starts where the full rental fits and avoids breaks/bookings."""
    if duration < 60 or duration > 720 or duration % 60:
        raise ValueError('duration must be 1–12 whole hours')
    start_min = opens.hour * 60 + opens.minute
    end_min = closes.hour * 60 + closes.minute
    if end_min <= start_min:
        raise ValueError('overnight rental schedule is not supported')
    if interval <= 0 or interval > 1440:
        raise ValueError('invalid booking interval')
    earliest = start_min
    if now is not None:
        if day < now.date():
            return []
        if day == now.date():
            cutoff = now + timedelta(hours=1)
            if cutoff.date() > day:
                return []
            cutoff_min = cutoff.hour * 60 + cutoff.minute + bool(cutoff.second or cutoff.microsecond)
            earliest += max(0, (cutoff_min - start_min + interval - 1) // interval) * interval
    occupied = [(t.hour * 60 + t.minute if isinstance(t, time) else t, minutes) for t, minutes in busy]
    occupied = [(start, start + minutes) for start, minutes in occupied]
    if break_start is not None and break_end is not None:
        a, b = break_start.hour * 60 + break_start.minute, break_end.hour * 60 + break_end.minute
        if b <= a:
            b += 1440
            occupied.append((a - 1440, b - 1440))
        occupied.append((a, b))
    return [f'{minute // 60:02d}:{minute % 60:02d}' for minute in range(earliest, end_min - duration + 1, interval)
            if not any(intervals_overlap(minute, minute + duration, a, b) for a, b in occupied)]
