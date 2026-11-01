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

    business = bool(re.search(r"\b(бизнес\w*|компани\w*|маркетинг\w*|продвижени\w*)\b", words))
    advice = bool(re.search(r"\b(порекоменд\w*|рекоменд\w*|посовет\w*|совет\w*|что\s+сделать|чем\s+заняться|какие\s+шаги|что\s+улучшить|с\s+чего\s+начать)\b", words))
    if business and advice and not re.match(r"^(сделай|создай|запусти|опубликуй)\b", words):
        return "recommend_today"
    return None


def quick_video_request(text: str) -> dict[str, str] | None:
    """Recognize explicit requests to CREATE an explainer/whiteboard video.

    A narrow fast path: it only extracts an explicitly provided topic and duration.
    Generic video requests, scripts, previews, cancellations and ambiguous wording
    still go to the language dispatcher. Approval and tenant checks remain in the
    existing whiteboard executor, not here.
    """
    source = (text or "").strip()
    words = re.sub(r"[^\w\s-]+", " ", source.casefold().replace("ё", "е"))
    words = " ".join(words.split())
    if not words or len(source) > 1000:
        return None
    if re.search(r"\b(?:не\s+(?:делай|создавай|запускай)|не\s+надо)\b", words):
        return None
    # The user may want a script ABOUT a video, not the actual rendering.
    if re.search(r"\b(?:сценарий|промпт|раскадровку|обложку|превью|идею|текст\s+для\s+видео)\b", words):
        return None
    if re.match(r"^(?:пожалуйста\s+)?(?:покажи|найди|открой|посмотри|где|отмени|удали)\b", words):
        return None
    if not re.search(r"\b(?:сделай|сделаем|создай|создадим|подготовь|подготовим|запусти|запустим|"
                     r"нарисуй|нарисуем|сгенерируй|сгенерируем|хочу|нужно|надо)\b", words[:120]):
        return None
    if not re.search(r"\b(?:видео[\w-]*|видеоролик\w*|ролик\w*)\b", words):
        return None
    if not re.search(r"\b(?:объясн\w*|объясня\w*|рисован\w*|рисунк\w*|анимац\w*|"
                     r"разбор\w*|объяснялк\w*|почему|whiteboard)\b|на\s+доске", words):
        return None

    # The subject is taken from the user's original words, not invented by a model.
    match = re.search(r"\bна\s+тему\s*[:—-]?\s*(.+)$", source, re.I | re.S)
    if match is None:
        match = re.search(r"\bпро\s+(.+)$", source, re.I | re.S)
    if match is None:
        match = re.search(r"\bпочему\s+(.+)$", source, re.I | re.S)
        if match is not None:
            topic = "Почему " + match.group(1)
        else:
            return None
    else:
        topic = match.group(1)
    topic = topic.strip().strip("«»\\\"' \\t\\r\\n").rstrip(" .")
    if not 8 <= len(topic) <= 400:
        return None

    minute = bool(re.search(r"\b(?:60\s*секунд\w*|(?:1|одн\w*)\s+минут\w*|минутн\w*)\b", words))
    return {"topic": topic, "length": "60" if minute else "30"}
