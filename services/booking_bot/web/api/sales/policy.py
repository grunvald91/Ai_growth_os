"""Business-owned capabilities, enforced outside the model."""
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field


class SalesPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = True
    booking_mode: Literal["client_confirmation", "operator_only"] = "client_confirmation"
    show_prices: bool = True
    recommend_specialists: bool = True
    check_availability: bool = True
    view_client_bookings: bool = True
    instructions: str = Field(default="", max_length=6000)
    prohibited_topics: str = Field(default="", max_length=2000)

    def allows(self, tool: str) -> bool:
        if tool == "handoff_to_human":
            return True
        if not self.enabled:
            return False
        return {
            "get_services": True, "get_service_details": True,
            "get_prices": self.show_prices,
            "get_specialists": self.recommend_specialists,
            "check_availability": self.check_availability,
            "get_my_bookings": self.view_client_bookings,
            "create_booking": self.booking_mode == "client_confirmation"
                and self.show_prices and self.check_availability,
        }.get(tool, False)

    def require(self, tool: str):
        if not self.allows(tool):
            raise HTTPException(403, "Это действие запрещено настройками чата выбранной компании")


def policy_for(business) -> SalesPolicy:
    saved = (business.sales_agent_settings or {}).get("policy", {})
    return SalesPolicy.model_validate({"instructions": (business.ai_instruction or "")[:6000], **saved})


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    policy: SalesPolicy


def policy_document(business):
    return {
        "business_id": business.id, "business_name": business.name,
        "revision": (business.sales_agent_settings or {}).get("revision", 0),
        "policy": policy_for(business).model_dump(),
        "business_rules": {
            "opening": str(business.working_hours_start) if business.working_hours_start else None,
            "closing": str(business.working_hours_end) if business.working_hours_end else None,
            "slot_interval_minutes": business.booking_interval_minutes,
            "break_start": str(business.break_start) if business.break_start else None,
            "break_end": str(business.break_end) if business.break_end else None,
            "booking_flow_order": business.booking_flow_order,
            "prepayment_enabled": business.prepayment_enabled,
            "prepayment_percent": business.prepayment_percent,
            "receipt_required": business.require_receipt_for_prepayment,
            "legal_consent_required": business.legal_documents_enabled,
            "cancellation_policy": business.cancellation_policy_text,
        },
        "always_forbidden": ["Менять цены и расписание", "Подтверждать получение денег",
            "Читать данные другого бизнеса или клиента", "Создавать запись без подтверждения клиента"],
        "coverage": [
            {"rule": "Часы, шаг бизнеса/ресурса, перерыв, занятость, минимальная длительность, выходные и дежурства", "status": "supported"},
            {"rule": "Фиксированная цена и фиксированные дополнительные услуги", "status": "supported"},
            {"rule": "Предоплата обычной записи и отдельные юридические согласия", "status": "supported"},
            {"rule": "Общий срок оплаты 10 минут для услуг и аренды, только при требуемой предоплате", "status": "supported"},
            {"rule": "Аренда залов по правилам страницы записи: пакеты длительности, почасовые тарифы по времени суток, индивидуальный тариф, предоплата бизнеса", "status": "supported"},
            {"rule": "Сложные/групповые тарифы услуг и абонементы", "status": "form_or_operator"},
            {"rule": "Ночной график работы", "status": "form_or_operator"},
            {"rule": "Изменение/отмена записи, проверка платежа и загрузка чека", "status": "booking_page_or_operator"},
        ],
    }
