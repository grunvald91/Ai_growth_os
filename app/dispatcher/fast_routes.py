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
    diagnostic = bool(re.search(r"\b(?:почему|ошиб\w*|сломал\w*|не\s+готов\w*|не\s+додел\w*|перерис\w*|передел\w*|перегенер\w*|возобнов\w*|повтори|исправ\w*|продолж\w*|додел\w*)\b", words))
    publishing = bool(re.search(r"\b(?:публикаци\w*|опубликов\w*|опублику\w*|отправ\w*|вылож\w*|перенес\w*|перенос\w*|отмен\w*)\b", words))
    if video and viewing and not creation and not diagnostic and not publishing:
        return "show_recent_videos"

    business = bool(re.search(r"\b(бизнес\w*|компани\w*|маркетинг\w*|продвижени\w*)\b", words))
    advice = bool(re.search(r"\b(порекоменд\w*|рекоменд\w*|посовет\w*|совет\w*|что\s+сделать|чем\s+заняться|какие\s+шаги|что\s+улучшить|с\s+чего\s+начать)\b", words))
    if business and advice and not re.match(r"^(сделай|создай|запусти|опубликуй)\b", words):
        return "recommend_today"
    return None


def quick_video_request(text: str) -> dict[str, str] | None:
    """Recognize explicit requests to CREATE an explainer/whiteboard video.

    A narrow fast path: it only extracts an explicitly provided topic and duration.
    Generic video requests (except explicit duration + topic), scripts, previews, cancellations and ambiguous wording
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
    # Editing and recovery requests must reach the semantic router, not a new video job.
    if re.search(r"\b(?:перерис\w*|передел\w*|перегенер\w*|возобнов\w*|повтори|исправ\w*|додел\w*|продолж\w*)\b", words[:120]):
        return None
    # The user may want a script ABOUT a video, not the actual rendering.
    if re.search(r"\b(?:сценарий|промпт|раскадровку|обложку|превью|идею|текст\s+для\s+видео)\b", words):
        return None
    if re.match(r"^(?:пожалуйста\s+)?(?:покажи|найди|открой|посмотри|где|отмени|удали)\b", words):
        return None
    # An explicit duration + video + subject is a complete nominal request, even without a verb.
    nominal = re.match(r"^(?:минутн\w*|полуминутн\w*)\s+(?:видео|видеоролик|ролик)\s+на\s+тему\s*:", source, re.I)
    if nominal and not re.search(r"[?;\n]|\b(?:и\s+(?:создай|сделай|покажи|опубликуй|отмени)|без\s+рисунк\w*)\b", source, re.I):
        topic = source[nominal.end():].strip().strip(' «»"\t\r\n').rstrip(" .")
        if 8 <= len(topic) <= 400:
            return {"topic": topic, "length": "30" if source.casefold().startswith("полуминут") else "60"}
    if not re.search(r"\b(?:сделай|сделаем|создай|создадим|подготовь|подготовим|запусти|запустим|"
                     r"нарисуй|нарисуем|сгенерируй|сгенерируем|хочу|нужно|надо)\b", words[:120]):
        return None
    if not re.search(r"\b(?:видео[\w-]*|видеоролик\w*|ролик\w*)\b", words):
        return None
    explainer = re.search(r"\b(?:объясн\w*|объясня\w*|рисован\w*|рисунк\w*|анимац\w*|"
                          r"разбор\w*|объяснялк\w*|почему|whiteboard)\b|на\s+доске", words)
    compound = re.search(r"\bвидео-?(?:объясн|разбор)\w*\b", words)
    # Explicit creation + duration + topic also forms a complete request.
    # Restrict this to a leading creation instruction, so a quoted command or
    # a discussion of somebody else's request cannot create a new draft.
    explicit = re.match(
        r"^(?:пожалуйста\s+|давай\s+)?(?:сделай|сделаем|создай|создадим|подготовь|подготовим|"
        r"сгенерируй|сгенерируем|запусти|запустим)\s+(?:мне\s+)?"
        r"(?:(?:минутн\w*|полуминутн\w*|нов\w+)\s+)?(?:видео|видеоролик|ролик)\b"
        r"(?:\s+(?:на\s+(?:одну\s+)?минуту|на\s+полминуты|(?:на\s+)?(?:30|60)\s*секунд\w*))?"
        r"\s+на\s+тему\b", words)
    duration = re.search(r"\b(?:минутн\w*|полуминутн\w*|(?:30|60)\s*секунд\w*|"
                         r"на\s+(?:одну\s+)?минуту|на\s+полминуты)\b", words[:explicit.end()] if explicit else "")
    if not (explainer or compound or (explicit and duration)):
        return None
    if explicit and re.search(r"\b(?:и\s+(?:создай|сделай|покажи)|опубликуй|отмени|"
                              r"без\s+рисунк\w*)\b", words):
        return None

    # The subject is taken from the user's original words, not invented by a model.
    match = re.search(r"\bна\s+тему\s*[:.—-]?\s*(.+)$", source, re.I | re.S)
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
    topic = topic.strip().strip(' «»"\t\r\n').rstrip(" .")
    # Duration is a render option, not part of the scientific topic.
    topic = re.sub(r"[,;]?\s*\b(?:30|60)\s*секунд\w*\s*$", "", topic, flags=re.I).strip()
    if not 8 <= len(topic) <= 400:
        return None

    minute = bool(re.search(r"\b(?:60\s*секунд\w*|(?:1|одн\w*)\s+минут\w*|на\s+минуту|минутн\w*)\b", words))
    return {"topic": topic, "length": "60" if minute else "30"}
