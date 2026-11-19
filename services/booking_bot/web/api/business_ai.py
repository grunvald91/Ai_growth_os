"""Grounded, read-only facts for ServGo Business AI.

The adapter never calls an LLM or modifies business data.
Its caller must provide the existing owner-scoped analytics overview.
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schemas import AnalyticsOverviewOut


def _metric(current: int | float | None, previous: int | float | None, source: str) -> dict:
    if current is None or previous is None:
        return {"current": current, "previous": previous, "delta": None,
                "change_percent": None, "source": source}
    delta = round(current - previous, 2)
    return {"current": current, "previous": previous, "delta": delta,
            "change_percent": round(delta * 100 / previous, 1) if previous > 0 else None,
            "source": source}


def build_business_agent_brief(*, business_id: int, overview: AnalyticsOverviewOut) -> dict:
    """Return provenance-bearing facts; do not invent causes or payments."""
    if not overview.daily:
        raise ValueError("Analytics overview must include the period's daily dates")

    current_start = overview.daily[0].date
    current_end = overview.daily[-1].date
    days = (current_end - current_start).days + 1
    previous_end = current_start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    kpi = overview.kpis

    value = _metric(kpi.revenue, kpi.revenue_prev, "kpis.revenue")
    visits = _metric(kpi.bookings, kpi.bookings_prev, "kpis.bookings")
    avg_check = _metric(kpi.avg_check, kpi.avg_check_prev, "kpis.avg_check")
    cancel = _metric(kpi.cancellation_rate, kpi.cancellation_rate_prev, "kpis.cancellation_rate")
    load = _metric(kpi.load_percent, kpi.load_percent_prev, "kpis.load_percent")

    findings: list[dict] = []
    if visits["delta"] < 0:
        findings.append({"code": "fewer_bookings", "metric": "bookings",
                         "text": f"Записей со статусом confirmed/completed на {abs(visits['delta']):g} меньше, чем за предыдущий период."})
    elif visits["delta"] > 0:
        findings.append({"code": "more_bookings", "metric": "bookings",
                         "text": f"Записей со статусом confirmed/completed на {visits['delta']:g} больше, чем за предыдущий период."})
    elif kpi.bookings == 0:
        findings.append({"code": "no_bookings", "metric": "bookings",
                         "text": "За период нет записей со статусом confirmed/completed."})

    if value["delta"] != 0:
        direction = "увеличилась" if value["delta"] > 0 else "уменьшилась"
        findings.append({"code": "booked_value_change", "metric": "booked_value_estimate_rub",
                         "text": f"Расчётная стоимость записей {direction} на {abs(value['delta']):g} ₽; это не фактически полученные платежи."})
    if cancel["delta"] > 0:
        findings.append({"code": "cancellations_rising", "metric": "cancellation_rate_percent",
                         "text": f"Доля отмен выросла на {cancel['delta']:g} процентного пункта."})

    return {
        "schema_version": 1,
        "business_id": business_id,
        "read_only": True,
        "data_source": f"/admin/analytics/{business_id}/overview",
        "period": {"start": current_start.isoformat(), "end": current_end.isoformat(), "days": days},
        "compare_to": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "metrics": {
            "booked_value_estimate_rub": value,
            "bookings": visits,
            "avg_booked_value_estimate_rub": avg_check,
            "cancellation_rate_percent": cancel,
            "approximate_load_percent": load,
        },
        "top_services": [{"id": row.id, "name": row.name, "bookings": row.count,
                          "booked_value_estimate_rub": row.revenue} for row in overview.top_services],
        "top_employees": [{"id": row.id, "name": row.name, "bookings": row.count,
                           "booked_value_estimate_rub": row.revenue} for row in overview.top_employees],
        "findings": findings,
        "data_limits": [
            "Сумма в рублях — оценка стоимости записанных услуг (включая дополнительные), НЕ фактическая оплата или прибыль.",
            "Число записей относится к занимающим ресурс визитам со статусами confirmed/completed; дополнительные услуги не создают отдельный визит.",
            "Сравнение выполнено с предыдущим периодом той же длины; изменение не доказывает причинно-следственную связь.",
            "Общая загрузка приблизительна и зависит от указанных рабочих часов и числа активных сотрудников; может быть недоступна.",
            "Списки услуг и сотрудников ограничены топ-5; нельзя делать вывод о самом слабом сотруднике по этому срезу.",
            "Нулевое значение за прошлый период не позволяет вычислить процент роста; используется null.",
        ],
        "permitted_next_step": "Дополнительный анализ или предложение действий владельцу; изменение записей, цен и рассылки здесь запрещены.",
    }
