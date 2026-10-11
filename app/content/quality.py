"""Deterministic editorial warnings, never fabricated performance estimates.

The checklist helps the owner review a post. It does not verify third-party
facts or substitute for editorial approval.
"""
from __future__ import annotations

import re


_GENERIC_OPENINGS = (
    "в современном мире",
    "в наше время",
    "ни для кого не секрет",
    "каждый из нас",
    "сегодня мы поговорим",
    "давайте разберёмся",
    "в эпоху цифровизации",
)


def post_quality_warnings(title: str, body: str, channel: str) -> list[str]:
    warnings = []
    text = " ".join((body or "").strip().split())
    header = " ".join((title or "").strip().split())
    if not text:
        return ["Текст пустой"]

    first = text[:150].casefold()
    if any(first.startswith(opener) for opener in _GENERIC_OPENINGS):
        warnings.append("Начало слишком общее: начните с конкретной боли или полезного факта")
    if len(header) < 15 or len(header.split()) < 3:
        warnings.append("Заголовок слишком общий: уточните предмет и пользу")
    if len(text) > 2600 and channel == "telegram":
        warnings.append("Для Telegram текст длинный: проверьте читаемость с телефона")
    if len(text) > 1000 and "\n" not in (body or ""):
        warnings.append("Нет разбивки на абзацы: материал может быть трудно читать")
    normalized_paragraphs = [
        " ".join(p.casefold().split())
        for p in re.split(r"\n\s*\n", body or "")
        if len(p.strip()) > 70
    ]
    if len(normalized_paragraphs) != len(set(normalized_paragraphs)):
        warnings.append("Повторяются целые абзацы: удалите дубли")
    if re.search(r"(?:\b100\s*%|\bгарантируем\s+(?:рост|продажи|результат)\b)", text, flags=re.IGNORECASE):
        warnings.append("Проверьте категоричное обещание результата и его доказательства")
    return warnings[:6]
