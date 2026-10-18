"""Official company registry lookups for intake.

The adapter is pluggable so other countries can be added later. The first adapter
uses DaData's party lookup (Russian ЕГРЮЛ/ЕГРИП data) by ИНН or ОГРН only: a lookup by
name could silently return a different company, so names are never auto-matched.
The API key stays inside this adapter and never reaches prompts or sources.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import httpx

from app.intake.contracts import IntakeSource
from app.intake.sources import make_source

DADATA_PARTY_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs/findById/party"
STATUS_LABELS = {
    "ACTIVE": "действует",
    "LIQUIDATING": "в процессе ликвидации",
    "LIQUIDATED": "ликвидирована",
    "BANKRUPT": "банкротство",
    "REORGANIZING": "в процессе реорганизации",
}
TYPE_LABELS = {"LEGAL": "юридическое лицо", "INDIVIDUAL": "индивидуальный предприниматель"}


def registry_identifier(text: str) -> str | None:
    """Return a ИНН (10/12 digits) or ОГРН/ОГРНИП (13/15 digits) found in owner text."""
    for match in re.findall(r"(?<!\d)\d[\d\s-]{8,20}\d(?!\d)", text or ""):
        digits = re.sub(r"\D", "", match)
        if len(digits) in {10, 12, 13, 15}:
            return digits
    return None


def _date(value) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()
    return None


def party_text(data: dict) -> str:
    name = data.get("name") or {}
    state = data.get("state") or {}
    address = data.get("address") or {}
    rows = [
        ("Полное наименование", name.get("full_with_opf")),
        ("Краткое наименование", name.get("short_with_opf")),
        ("Тип", TYPE_LABELS.get(data.get("type"), data.get("type"))),
        ("ИНН", data.get("inn")),
        ("ОГРН", data.get("ogrn")),
        ("Основной ОКВЭД", data.get("okved")),
        ("Адрес регистрации", address.get("value") if isinstance(address, dict) else None),
        ("Статус", STATUS_LABELS.get(state.get("status"), state.get("status"))),
        ("Дата регистрации", _date(state.get("registration_date"))),
    ]
    return "\n".join(f"{label}: {value}" for label, value in rows if value)


class DadataRegistryLookup:
    def __init__(self, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 10):
        if not api_key:
            raise ValueError("DaData API key is required")
        self._api_key, self._transport, self._timeout = api_key, transport, timeout

    async def lookup(self, identifier: str) -> IntakeSource:
        number = registry_identifier(identifier)
        if number is None:
            return make_source("official_registry", "реестр: поиск по названию", status="unavailable",
                               note="Для проверки в реестре пришлите ИНН или ОГРН — по названию можно найти чужую компанию.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout) as client:
                response = await client.post(DADATA_PARTY_URL, json={"query": number, "count": 1}, headers={
                    "Authorization": f"Token {self._api_key}", "Accept": "application/json"})
            response.raise_for_status()
            suggestions = response.json().get("suggestions") or []
        except (httpx.HTTPError, ValueError, AttributeError):
            # Errors can echo request headers; never let them reach the owner or the model.
            raise OSError("registry_unavailable") from None
        reference = f"ЕГРЮЛ/ЕГРИП (DaData): {number}"
        if not suggestions or not isinstance(suggestions[0], dict):
            return make_source("official_registry", reference, status="unavailable",
                               note="Компания с таким ИНН/ОГРН в реестре не найдена. Проверьте номер.")
        text = party_text(suggestions[0].get("data") or {})
        if not text:
            return make_source("official_registry", reference, status="unavailable",
                               note="Реестр не вернул сведений о компании.")
        return make_source("official_registry", reference, text)


def registry_from_settings(settings) -> DadataRegistryLookup | None:
    key = getattr(settings, "dadata_api_key", None)
    return DadataRegistryLookup(key) if key else None
