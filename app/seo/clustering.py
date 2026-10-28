"""Deterministic grouping of raw queries into intent clusters mapped to funnel stages."""
import re

from app.seo.contracts import Engine, FunnelStage, Intent, QueryCluster

_STOP = frozenset("и в во не на с со по к у о об от до из за для или а но как что это при без под над про же ли бы".split())
_TRANSACTIONAL = ("купить", "заказать", "записаться", "запись на", "скачать", "регистрация", "тариф", "подключить",
                  "оформить", "установить", "войти", "личный кабинет", "демо", "попробовать")
_COMMERCIAL = ("цена", "стоимость", "сколько стоит", "отзывы", "лучший", "лучшие", "топ", "рейтинг", "сравнение",
               " vs ", "или", "альтернатив", "аналог", "что выбрать", "какой выбрать", "какую выбрать", "обзор")
_INFORMATIONAL = ("как", "что такое", "зачем", "почему", "какой", "какие", "можно ли", "пример", "идеи", "инструкция",
                  "способы", "советы", "гайд", "шаблон", "своими руками")
_STAGE: dict[Intent, FunnelStage] = {
    "informational": "awareness", "navigational": "awareness", "commercial": "consideration", "transactional": "decision",
}


def normalize(query: str) -> str:
    text = query.casefold().replace("ё", "е")
    text = re.sub(r"[^\w\s-]", " ", text)
    return " ".join(text.split())


def classify_intent(query: str) -> Intent:
    text = f" {normalize(query)} "
    if any(marker in text for marker in _TRANSACTIONAL):
        return "transactional"
    if any(marker in text for marker in _COMMERCIAL):
        return "commercial"
    if any(text.strip().startswith(marker) or f" {marker} " in text for marker in _INFORMATIONAL):
        return "informational"
    return "informational"


def stems(query: str) -> frozenset[str]:
    """Crude Russian stemming: the first six letters of each content word groups word forms together."""
    words = [w for w in normalize(query).split() if w not in _STOP and len(w) > 2]
    return frozenset(w[:6] for w in words)


def _similar(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.6


def cluster_queries(raw: dict[Engine, set[str]]) -> list[QueryCluster]:
    """raw maps an engine to the queries it suggested. Clusters never mix intents."""
    seen: dict[str, tuple[str, set[Engine]]] = {}
    for engine, queries in raw.items():
        for query in queries:
            key = normalize(query)
            if len(key) < 2:
                continue
            seen.setdefault(key, (query.strip(), set()))[1].add(engine)
    clusters: list[dict] = []
    for key in sorted(seen, key=lambda k: (len(k.split()), k)):
        text, engines = seen[key]
        intent, token_set = classify_intent(text), stems(text)
        for cluster in clusters:
            if cluster["intent"] == intent and _similar(cluster["stems"], token_set):
                cluster["queries"].append(text)
                cluster["engines"] |= engines
                break
        else:
            clusters.append({"head": text, "intent": intent, "stems": token_set, "queries": [text], "engines": set(engines)})
    return [
        QueryCluster(id=f"c{index:03d}", head_query=c["head"], intent=c["intent"], stage=_STAGE[c["intent"]],
                     queries=c["queries"], engines=sorted(c["engines"]))
        for index, c in enumerate(clusters, start=1)
    ]
