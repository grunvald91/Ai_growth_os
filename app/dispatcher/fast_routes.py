"""Fast, deterministic routing for read-only owner requests.

These two commands map to existing platform facts; spending minutes asking an
LLM to decide whether they are supported is unnecessary and error-prone.
The model remains responsible for all other free-form requests.
"""
from __future__ import annotations

import re


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

    # Read-only owner questions must work even if the model service is down.
    # Never let a status question launch paid work or mutate a plan.
    action_prefix = re.match(
        r"^(?:создай|составь|подготовь|сделай|запусти|опубликуй|утверди|перепиши|измени|назначь|отмени)\\b",
        words,
    )
    if not action_prefix:
        if re.search(r"\\b(контент[ -]?план|план[ауе]?\\s+публикаци\\w*|план\\s+контента)\\b", words):
            return "show_plan"
        if re.search(
            r"\\b(что\\s+(?:у\\s+нас\\s+)?(?:дальше|следующ\\w*|по\\s+плану)|"
            r"какой\\s+следующ\\w*\\s+шаг|"
            r"где\\s+мы\\s+(?:сейчас\\s+)?по\\s+плану|"
            r"на\\s+каком\\s+этапе|"
            r"что\\s+(?:сейчас\\s+)?по\\s+плану)\\b",
            words,
        ):
            return "show_next_steps"
        if re.search(r"\\b(что\\s+в\\s+работе|какие\\s+задани\\w*\\s+в\\s+работе|статус\\s+задач\\w*)\\b", words):
            return "show_work"
        if re.search(r"\\b(что\\s+жд[её]т\\s+(?:проверки|согласования)|что\\s+готово\\s+на\\s+проверку)\\b", words):
            return "show_review"

    business = bool(re.search(r"\b(бизнес\w*|компани\w*|маркетинг\w*|продвижени\w*)\b", words))
    advice = bool(re.search(r"\b(порекоменд\w*|рекоменд\w*|посовет\w*|совет\w*|что\s+сделать|чем\s+заняться|какие\s+шаги|что\s+улучшить|с\s+чего\s+начать)\b", words))
    if business and advice and not re.match(r"^(сделай|создай|запусти|опубликуй)\b", words):
        return "recommend_today"
    return None
