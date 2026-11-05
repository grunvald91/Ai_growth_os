"""Explainable, tenant-scoped name suggestions. Never chooses or books a service."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

STOP_WORDS = frozenset({
    "и", "или", "в", "во", "на", "за", "по", "с", "со", "к", "для", "до",
    "от", "у", "мне", "хочу", "надо", "нужна", "нужно", "пожалуйста",
    "есть", "можно", "добавить", "сделать", "услуга", "услугу", "еще",
})


def normalized_tokens(text: str) -> list[str]:
    tokens = []
    for word in re.findall(r"[а-яa-z0-9]+", text.casefold().replace("ё", "е")):
        if word in STOP_WORDS:
            continue
        if word.startswith(("помыв", "мойк", "мыть", "мытье", "помой")):
            word = "мойка"
        elif word.startswith(("колес", "шин")) and not word.startswith("шиномонт"):
            # Terminology is a *possible* match, never proof of equal scope.
            word = "колесо"
        elif word.startswith(("стриж", "подстр", "стриг")):
            word = "стрижка"
        elif word.startswith(("маникюр", "маник")):
            word = "маникюр"
        elif word.startswith(("массаж", "массир")):
            word = "массаж"
        elif len(word) > 5:
            word = re.sub(r"(иями|ями|ами|ого|ему|ыми|ими|ий|ый|ой|ая|ое|ые|ов|ев|ом|ах|ях|ам|ям|ей|ие|ую|их|ым|ой|а|я|ы|и|е|у|ю)$", "", word)
        if word:
            tokens.append(word)
    return tokens


def score_service(query: str, name: str, description: str = "") -> float:
    wanted = list(dict.fromkeys(normalized_tokens(query)))
    have = list(dict.fromkeys(normalized_tokens(name)))
    if not wanted or not have:
        return 0.0
    matches = sum(any(token == candidate or (
        min(len(token), len(candidate)) >= 4 and
        SequenceMatcher(None, token, candidate).ratio() >= 0.83
    ) for candidate in have) for token in wanted)
    coverage = matches / len(wanted)
    name_coverage = matches / len(have)
    score = .72 * coverage + .28 * name_coverage
    if matches == 0 and description:
        desc = set(normalized_tokens(description[:250]))
        if any(token in desc for token in wanted):
            return 0.2
    return round(score, 3) if matches else 0.0


def rank_services(services: list, query: str, *, max_results: int = 8) -> list:
    scored = [(score_service(query, s.name, s.description or ""), s) for s in services]
    scored.sort(key=lambda entry: (-entry[0], entry[1].sort_order or 0, entry[1].id))
    return [(service, score) for score, service in scored if score >= 0.20][:max_results]
