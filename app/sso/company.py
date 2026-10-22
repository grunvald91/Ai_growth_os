"""Turn what a ServGo business shows publicly into the first description of a studio company."""
from __future__ import annotations

MAX_ABOUT = 12000  # The company intake accepts at most this much text.
MIN_ABOUT = 30


def _price(value) -> str:
    return f"{value:,}".replace(",", " ") + " ₽" if isinstance(value, int) and value > 0 else ""


def about_text(data: dict) -> str:
    """Plain Russian text for the intake. Only facts the business already published in ServGo."""
    name = str(data.get("name") or "").strip()
    lines = [f"{name} — заведение на платформе онлайн-записи ServGo."]
    if data.get("business_type"):
        lines.append(f"Тип бизнеса в ServGo: {data['business_type']}.")
    for key, label in (("description", "Описание"), ("address", "Адрес"), ("phone", "Телефон"),
                       ("working_hours", "Часы работы"), ("break_hours", "Перерыв")):
        value = str(data.get(key) or "").strip()
        if value:
            lines.append(f"{label}: {value}")
    if data.get("prepayment_percent"):
        lines.append(f"Онлайн-запись с предоплатой {data['prepayment_percent']}%.")
    services = [item for item in data.get("services") or [] if isinstance(item, dict) and item.get("name")]
    if services:
        lines.append("Услуги и цены:")
        for item in services:
            parts = [str(item["name"]).strip()]
            extra = ", ".join(part for part in (
                _price(item.get("price")),
                f"{item['duration_minutes']} мин" if isinstance(item.get("duration_minutes"), int) else "",
                str(item.get("tariff") or "").strip()) if part)
            line = "- " + parts[0] + (f" — {extra}" if extra else "")
            if str(item.get("description") or "").strip():
                line += f". {str(item['description']).strip()}"
            lines.append(line)
    text = "\n".join(lines)
    if len(text) > MAX_ABOUT:
        text = text[:MAX_ABOUT].rsplit("\n", 1)[0]
    return text
