"""Readiness checks for showing launch setup hints to business owners."""

from bot.utils.business_types import is_venue_rental_type


def build_business_readiness_warnings(business, services, workers) -> list[str]:
    """Return owner-facing setup hints for a business card."""
    warnings = []
    is_venue = is_venue_rental_type(getattr(business, "business_type", ""))

    if not services:
        if is_venue:
            warnings.append("Добавьте хотя бы один вариант времени аренды")
        else:
            warnings.append("Добавьте хотя бы одну услугу")

    if not workers:
        if is_venue:
            warnings.append("Добавьте хотя бы одно помещение/зал")
        else:
            warnings.append("Добавьте хотя бы одного сотрудника/мастера")

    if not getattr(business, "phone", None):
        warnings.append("Укажите телефон для связи")

    if not getattr(business, "address", None):
        warnings.append("Укажите адрес")

    if not getattr(business, "working_hours_start", None) or not getattr(business, "working_hours_end", None):
        warnings.append("Укажите время работы")

    return warnings
