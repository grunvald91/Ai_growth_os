"""Fast, deterministic routing for read-only owner requests.

These two commands map to existing platform facts; spending minutes asking an
LLM to decide whether they are supported is unnecessary and error-prone.
The model remains responsible for all other free-form requests.
"""
from __future__ import annotations

import re


def requested_story_count(text: str) -> int | None:
    """Extract an explicit 3–10 screen count; never infer one from duration."""
    words = (text or "").casefold().replace("ё", "е")
    numerals = {"три": 3, "трех": 3, "четыре": 4, "четырех": 4,
                "пять": 5, "пяти": 5, "шесть": 6, "шести": 6,
                "семь": 7, "семи": 7, "восемь": 8, "восьми": 8,
                "девять": 9, "девяти": 9, "десять": 10, "десяти": 10}
    variants = r"(?:3|4|5|6|7|8|9|10|" + "|".join(numerals) + r")"
    kinds = r"(?:сторис|истори\w*|экран\w*)"
    before = re.search(r"\b(" + variants + r")\s+(?:\w+\s+){0,2}" + kinds + r"\b", words)
    after = re.search(r"\b" + kinds + r"\s+(?:из\s+)?(" + variants + r")\b", words)
    match = before or after
    if not match:
        return None
    token = match.group(1)
    return int(token) if token.isdigit() else numerals.get(token)


def quick_capability(text: str) -> str | None:
    words = re.sub(r"[^\w\s-]+", " ", (text or "").casefold().replace("ё", "е"))
    words = " ".join(words.split())
    if not words:
        return None

    video = bool(re.search(r"\bвидео\b|\bвидеоролик\w*\b|\bролик\w*\b|\bанимаци\w*\b", words))
    creation = bool(re.match(r"^(сделай|создай|сгенерируй|запусти|нарисуй|подготовь)\s+(?:мне\s+)?(?:нов\w+\s+)?(?:видео|видеоролик|ролик|анимаци)", words))
    viewing = bool(re.search(r"\b(покажи|показать|посмотреть|посмотри|найди|открой|увидеть|где|готов\w*|последн\w*|недавн\w*|сделанн\w*|созданн\w*|запущенн\w*|мои|моих|своих|статус)\b", words))
    if video and viewing and not creation:
        return "show_recent_videos"

    business = bool(re.search(r"\b(бизнес\w*|компани\w*|маркетинг\w*|продвижени\w*)\b", words))
    advice = bool(re.search(r"\b(порекоменд\w*|рекоменд\w*|посовет\w*|совет\w*|что\s+сделать|чем\s+заняться|какие\s+шаги|что\s+улучшить|с\s+чего\s+начать)\b", words))
    if business and advice and not re.match(r"^(сделай|создай|запусти|опубликуй)\b", words):
        return "recommend_today"
    return None
