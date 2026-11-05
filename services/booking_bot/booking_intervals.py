"""Pure interval primitives for ServGo booking safety.

This module intentionally has no database, bot, or environment imports so it
can be tested in isolation.
"""
from datetime import datetime


def intervals_overlap(
    start: datetime,
    end: datetime,
    other_start: datetime,
    other_end: datetime,
) -> bool:
    """Half-open interval overlap: touching endpoints are not a conflict."""
    return start < other_end and other_start < end
