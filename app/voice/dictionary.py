"""Dictionaries for dictation: general and per project. Stage 1 reads versioned JSON files.

File format (products/voice/global_dictionary.json, products/<slug>/voice_dictionary.json):
  {"terms": [{"term": "VIN", "heard_as": ["вин", "win"], "note": "идентификатор автомобиля", "hint": true}]}
`heard_as` lists how recognition usually writes the term. `hint: true` puts the term into the recognizer's prompt
on every call (use it for the few terms that matter most).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_WORD = re.compile(r"[\w'-]+", re.UNICODE)
_MATCH_RATIO = 0.8
# Whisper reads at most ~224 tokens of prompt; Russian needs 2-3 characters per token.
PROMPT_BUDGET_CHARS = 420
SELECTED_LIMIT = 10


@dataclass(frozen=True)
class Term:
    term: str
    heard_as: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""
    hint: bool = False


def _load(path: Path) -> list[Term]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    terms = []
    for item in raw.get("terms", []) if isinstance(raw, dict) else []:
        if not isinstance(item, dict) or not isinstance(item.get("term"), str) or not item["term"].strip():
            continue
        heard = tuple(str(value).strip().lower() for value in item.get("heard_as", []) if str(value).strip())
        terms.append(Term(term=item["term"].strip(), heard_as=heard, note=str(item.get("note", "")).strip(),
                          hint=bool(item.get("hint", False))))
    return terms


def load_dictionary(products_root: Path, project: str | None) -> list[Term]:
    """General dictionary first, then the project's. A project term overrides a general one with the same spelling."""
    merged: dict[str, Term] = {term.term.lower(): term for term in _load(products_root / "voice" / "global_dictionary.json")}
    if project and SLUG.match(project):
        for term in _load(products_root / project / "voice_dictionary.json"):
            merged[term.term.lower()] = term
    return list(merged.values())


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def _score(variant: str, words: list[str]) -> float:
    """Best similarity of a spoken variant (one or several words) to any window of the transcript."""
    size = max(1, len(variant.split()))
    best = 0.0
    for start in range(0, max(1, len(words) - size + 1)):
        window = " ".join(words[start:start + size])
        if not window:
            continue
        if window == variant:
            return 1.0
        best = max(best, SequenceMatcher(None, variant, window).ratio())
    return best


def select_for_text(terms: list[Term], transcript: str, limit: int = SELECTED_LIMIT) -> list[Term]:
    """Terms whose sound-alikes appear in the finished transcript: a handful instead of the whole dictionary."""
    words = _words(transcript)
    if not words:
        return []
    scored: list[tuple[float, Term]] = []
    for entry in terms:
        variants = {entry.term.lower(), *entry.heard_as}
        best = max((_score(variant, words) for variant in variants), default=0.0)
        if best >= _MATCH_RATIO:
            scored.append((best, entry))
    scored.sort(key=lambda pair: (-pair[0], pair[1].term))
    return [entry for _, entry in scored[:limit]]


def recognizer_prompt(terms: list[Term], context: list[str]) -> str | None:
    """Short prompt for the recognizer: the tail of the last phrase, then the key terms (most important last)."""
    hint_terms = [entry.term for entry in terms if entry.hint]
    tail = " ".join(context[-1:]).strip()[-140:] if context else ""
    parts = []
    if tail:
        parts.append(tail)
    if hint_terms:
        parts.append("Термины: " + ", ".join(hint_terms) + ".")
    prompt = " ".join(parts).strip()
    if not prompt:
        return None
    return prompt[-PROMPT_BUDGET_CHARS:]


def describe(terms: list[Term]) -> str:
    lines = []
    for entry in terms:
        line = f"- {entry.term}"
        if entry.heard_as:
            line += " (может звучать как: " + ", ".join(entry.heard_as) + ")"
        if entry.note:
            line += f" — {entry.note}"
        lines.append(line)
    return "\n".join(lines)
