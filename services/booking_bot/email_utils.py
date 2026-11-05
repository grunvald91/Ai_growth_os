"""Pure helpers for optional booking email addresses."""
from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_optional_email(value: str | None) -> str | None:
    """Normalize an optional email or raise ValueError for malformed input."""
    if value is None:
        return None
    email = value.strip().lower()
    if not email:
        return None
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        raise ValueError("Некорректный email")
    return email
