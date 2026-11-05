"""Tenant-bound catalog and booking adapters; business/client IDs never come from the LLM."""
from __future__ import annotations

import json
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from database.models import Business, Service, Employee, EmployeeService, EmployeeDayOff, Booking, VenueTimePricing
from database.booking_safety import lock_resource_days
from ..routes import client
from ..schemas import CreateBookingIn
from .policy import policy_for, SalesPolicy
from .schedule import resource_works
from .service_match import rank_services


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogArgs(ToolArgs):
    query: str = Field(default="", max_length=200)


class ServiceArgs(ToolArgs):
    service_id: int = Field(gt=0)


class PriceArgs(ServiceArgs):
    employee_id: int | None = Field(default=None, gt=0)
    solo: bool = False
    additional_service_ids: list[int] = Field(default_factory=list, max_length=8)
    date: Date | None = None
    time: str | None = Field(default=None, pattern=r"^\d{2}:\d{2}$")


class AvailabilityArgs(ServiceArgs):
    date: Date
    employee_id: int | None = Field(default=None, gt=0)
    solo: bool = False


class BookingArgs(AvailabilityArgs):
    employee_id: int = Field(gt=0)
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    additional_service_ids: list[int] = Field(default_factory=list, max_length=8)


class HandoffArgs(ToolArgs):
    reason: str = Field(min_length=1, max_length=500)


ARG_MODELS = {
    "get_services": CatalogArgs, "get_service_details": ServiceArgs,
    "get_prices": PriceArgs, "get_specialists": ServiceArgs,
    "check_availability": AvailabilityArgs, "create_booking": BookingArgs,
    "handoff_to_human": HandoffArgs, "get_my_bookings": ToolArgs,
}
DESCRIPTIONS = {
    "get_services": "Каталог услуг выбранной компании. Начни с него, не придумывай услуги.",
    "get_service_details": "Описание, длительность, инструкции и ограничения реальной услуги.",
    "get_prices": "Реальная цена по правилам страницы записи. Для площадки цена зависит от зала, даты и времени "
                  "(почасовые тарифы) и формата solo=true (индивидуальный тариф зала).",
    "get_specialists": "Активные специалисты/залы для услуги; для залов — вместимость, почасовые тарифы и индивидуальный тариф.",
    "check_availability": "Реальные свободные окна по услуге, дате и ресурсу. Длительность задаёт выбранная услуга (пакет).",
    "create_booking": "Подготовь карточку записи после выбора услуги, специалиста и времени. Не создаёт запись до подтверждения клиентом.",
    "handoff_to_human": "Передай диалог оператору по просьбе клиента или когда требуется человек. ИИ замолчит до возврата оператором.",
    "get_my_bookings": "Покажи записи авторизованного клиента в выбранной компании и ссылки для управления ими.",
}
TOOL_SCHEMAS = [{"type": "function", "function": {
    "name": name, "description": DESCRIPTIONS[name], "parameters": model.model_json_schema(),
}} for name, model in ARG_MODELS.items()]


class ServGoTools:
    def __init__(self, db, business: Business, user=None):
        self.db, self.business, self.user = db, business, user
        self.policy = policy_for(business)

    async def require_tool(self, name):
        saved = (await self.db.execute(select(Business.sales_agent_settings)
            .where(Business.id == self.business.id))).scalar_one()
        self.policy = SalesPolicy.model_validate((saved or {}).get("policy", {}))
        self.policy.require(name)

    async def service(self, service_id: int) -> Service:
        service = (await self.db.execute(select(Service).where(
            Service.id == service_id, Service.business_id == self.business.id,
            Service.is_active.is_(True)))).scalar_one_or_none()
        if not service:
            raise HTTPException(404, "Услуга не найдена в выбранной компании")
        return service

    async def employee(self, employee_id: int, service: Service) -> Employee:
        employees = await self.specialists(service)
        employee = next((e for e in employees if e.id == employee_id), None)
        if not employee:
            raise HTTPException(404, "Специалист не оказывает эту услугу в выбранной компании")
        return employee

    async def specialists(self, service: Service) -> list[Employee]:
        employees = (await self.db.execute(select(Employee).where(
            Employee.business_id == self.business.id, Employee.is_active.is_(True),
            Employee.role != "administrator").order_by(Employee.id))).scalars().all()
        links = (await self.db.execute(select(EmployeeService.employee_id, EmployeeService.service_id)
            .join(Employee, Employee.id == EmployeeService.employee_id)
            .where(Employee.business_id == self.business.id))).all()
        by_employee = {}
        for employee_id, service_id in links:
            by_employee.setdefault(employee_id, set()).add(service_id)
        allowed = None
        if service.available_employee_ids:
            try:
                allowed = set(json.loads(service.available_employee_ids))
            except (ValueError, TypeError):
                return []  # Invalid configuration cannot make all resources eligible.
        return [e for e in employees
                if (not by_employee.get(e.id) or service.id in by_employee[e.id])
                and (allowed is None or e.id in allowed)]

    @staticmethod
    def service_data(service):
        return {"id": service.id, "name": service.name, "description": service.description,
                "duration_minutes": service.duration_minutes, "price": service.price,
                "is_additional": service.is_additional, "pricing_type": service.pricing_type,
                "tariff_description": service.tariff_description,
                "instructions": (service.ai_instruction or "")[:3000]}

    async def addons(self, ids: list[int]) -> list[Service]:
        if len(ids) != len(set(ids)):
            raise HTTPException(422, "Дополнительные услуги не должны повторяться")
        services = [await self.service(service_id) for service_id in ids]
        if any(not service.is_additional for service in services):
            raise HTTPException(422, "Выбрана неверная дополнительная услуга")
        return services

    def is_venue(self) -> bool:
        return self.business.business_type == "venue_rental"

    async def quote(self, args: PriceArgs) -> dict:
        """Цена по правилам страницы записи (create_booking_visit), без отдельного движка аренды."""
        service = await self.service(args.service_id)
        employee = await self.employee(args.employee_id, service) if args.employee_id else None
        addons = await self.addons(args.additional_service_ids)
        duration = service.duration_minutes or 30
        result = {"service_id": service.id, "currency": "RUB", "amount": service.price,
                  "duration_minutes": duration, "price_requires_confirmation": False,
                  "booking_supported": True, "booking_url": f"/book/{self.business.id}"}
        if args.solo and not (self.is_venue() and employee and employee.solo_price_per_hour):
            raise HTTPException(422, "Индивидуальный тариф доступен только для зала, где он задан")
        addon_total = 0
        if any(s.price is None or s.pricing_type not in (None, "fixed") or s.tariff_description for s in addons):
            result.update(price_requires_confirmation=True, booking_supported=False)
        else:
            # Почасовые доп. услуги — 1 час, как по умолчанию на странице записи и на сервере.
            addon_total = sum(s.price for s in addons)
        if self.is_venue():
            if employee is None:
                result.update(amount=None, price_depends_on_resource=True)
            elif args.date and args.time:
                base = await client._visit_base_price(self.db, self.business, [service], employee,
                    is_solo=args.solo, start=args.time, duration=duration, occ_date=args.date)
                result["amount"] = base + addon_total
            else:
                has_rules = (await self.db.execute(select(VenueTimePricing.id)
                    .where(VenueTimePricing.employee_id == employee.id).limit(1))).first()
                if args.solo:
                    result["amount"] = int(employee.solo_price_per_hour * max(1, duration / 60)) + addon_total
                elif has_rules:
                    result.update(amount=None, price_depends_on_time=True)
                else:
                    result["amount"] = (service.price or 0) + addon_total
        else:
            if (service.pricing_type not in (None, "fixed") or service.tariff_description
                    or service.price is None or service.is_hourly_price or duration <= 0):
                result.update(amount=None, price_requires_confirmation=True, booking_supported=False)
            elif result["booking_supported"]:
                result["amount"] = service.price + addon_total
            if employee:
                rules = (await self.db.execute(select(VenueTimePricing.id)
                    .where(VenueTimePricing.employee_id == employee.id).limit(1))).first()
                if rules:
                    result.update(price_requires_confirmation=True, booking_supported=False)
        if (self.business.working_hours_start and self.business.working_hours_end
            and self.business.working_hours_end <= self.business.working_hours_start):
            # Existing SlotOut carries only HH:MM, so next-day starts cannot be
            # safely confirmed against the requested calendar date by this adapter.
            result["booking_supported"] = False
        if result["price_requires_confirmation"]:
            result["amount"] = None
        result["prepayment_percent"] = (self.business.prepayment_percent or 100) if self.business.prepayment_enabled else 0
        result["receipt_required"] = bool(self.business.require_receipt_for_prepayment)
        result["payment_confirmation"] = "operator"
        # Округление как в create_booking_visit.
        result["prepayment_amount"] = (
            int(result["amount"] * result["prepayment_percent"] / 100) if result["amount"] is not None else None)
        return result

    async def availability(self, args: AvailabilityArgs) -> dict:
        if (self.business.working_hours_start and self.business.working_hours_end
            and self.business.working_hours_end <= self.business.working_hours_start):
            raise HTTPException(422, "Для ночного графика используйте обычную форму записи или оператора")
        service = await self.service(args.service_id)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        if args.date < today or args.date > today + timedelta(days=366):
            raise HTTPException(422, "Выберите дату в течение ближайшего года")
        if service.is_additional:
            raise HTTPException(422, "Сначала выберите основную услугу")
        employees = ([await self.employee(args.employee_id, service)] if args.employee_id
                     else await self.specialists(service))
        if args.solo:
            employees = [e for e in employees if self.is_venue() and e.solo_price_per_hour]
        duration = service.duration_minutes or 30
        slots = []
        for employee in employees:
            if not await resource_works(self.db, employee, args.date, duration):
                continue
            # Те же окна, что на странице записи: длительность — из услуги.
            found = await client.get_slots(self.business.id, args.date, service.id, employee.id,
                duration, False, None, self.db)
            slots.extend({"time": s.time, "employee_id": employee.id, "employee_name": employee.name}
                         for s in found if s.available)
        slots.sort(key=lambda s: (s["time"], s["employee_id"]))
        return {"date": args.date.isoformat(), "service_id": service.id, "service_name": service.name,
                "duration_minutes": duration, "solo": args.solo, "slots": slots}

    async def prepare_booking(self, args: BookingArgs) -> dict:
        await self.require_tool("create_booking")
        service = await self.service(args.service_id)
        employee = await self.employee(args.employee_id, service)
        quote = await self.quote(PriceArgs.model_validate(args.model_dump()))
        if not quote["booking_supported"]:
            raise HTTPException(422, "Этот тариф оформляется через обычную форму записи или оператора")
        slots = await self.availability(AvailabilityArgs.model_validate({
            k: v for k, v in args.model_dump().items() if k in AvailabilityArgs.model_fields}))
        if not any(s["time"] == args.time and s["employee_id"] == args.employee_id for s in slots["slots"]):
            raise HTTPException(409, "Это время уже недоступно. Выберите другое окно")
        addons = await self.addons(args.additional_service_ids)
        addon_names = [s.name for s in addons]
        amount = f"{quote['amount']} ₽" if quote["amount"] is not None else "цену подтверждает менеджер"
        description = (f"{self.business.name}: {service.name}, {employee.name}, "
                       f"{args.date.isoformat()} в {args.time}, {quote['duration_minutes']} мин, {amount}")
        if args.solo:
            description += ", индивидуальный формат"
        if addon_names:
            description += ". Доп. услуги: " + ", ".join(addon_names)
        if quote["prepayment_amount"]:
            description += f". Предоплата: {quote['prepayment_amount']} ₽ ({quote['prepayment_percent']}%)"
            description += ". Сообщите об отправке предоплаты в течение 10 минут после создания брони"
        if quote["receipt_required"] and quote["prepayment_amount"]:
            description += ". Для проверки предоплаты требуется чек на странице записи"
        # Структурированная карточка для компактного подтверждения в чате.
        summary = {"business_name": self.business.name, "service_name": service.name,
                   "resource_name": employee.name, "resource_label": "Зал" if self.is_venue() else "Специалист",
                   "date": args.date.isoformat(), "time": args.time,
                   "duration_minutes": quote["duration_minutes"], "solo": args.solo,
                   "addons": addon_names, "amount": quote["amount"],
                   "prepayment_amount": quote["prepayment_amount"] or None,
                   "prepayment_percent": quote["prepayment_percent"] or None,
                   "receipt_required": bool(quote["receipt_required"] and quote["prepayment_amount"])}
        return {"description": description, "summary": summary,
                "args": args.model_dump(mode="json"), "quote": quote,
                "requires_contact": not self.user,
                "requires_legal_consent": bool(self.business.legal_documents_enabled),
                "business_id": self.business.id, "cancellation_policy": self.business.cancellation_policy_text}

    async def catalog_hint(self, message: str) -> str:
        """Small, trusted catalog snapshot saves an avoidable first LLM/tool round."""
        if not self.policy.enabled:
            return ""
        services = list((await self.db.execute(select(Service).where(
            Service.business_id == self.business.id, Service.is_active.is_(True))
            .order_by(Service.sort_order, Service.id).limit(80))).scalars())
        matched = rank_services(services, message, max_results=5)
        display = services[:35]
        # Include relevant items beyond the first page without inventing an ID.
        seen = {s.id for s in display}
        display.extend(s for s, _ in matched if s.id not in seen)
        catalog = [{"id": s.id, "name": s.name[:100],
                    "additional": bool(s.is_additional),
                    **({"price": s.price} if self.policy.show_prices else {})}
                   for s in display]
        possible = [{"id": s.id, "name": s.name[:100], "score": score}
                    for s, score in matched]
        return json.dumps({"catalog": catalog, "possibly_related": possible,
                           "truncated": len(services) > len(display)}, ensure_ascii=False)

    async def execute_read(self, name: str, args: ToolArgs) -> dict:
        await self.require_tool(name)
        if name == "get_services":
            query = select(Service).where(Service.business_id == self.business.id, Service.is_active.is_(True))
            services = list((await self.db.execute(query.order_by(Service.sort_order, Service.id).limit(200))).scalars())
            if args.query.strip():
                ranked = rank_services(services, args.query.strip(), max_results=15)
                services = [service for service, _ in ranked]
            else:
                services = services[:50]
            return {"services": [{"id": s.id, "name": s.name, "price": s.price if self.policy.show_prices else None,
                "duration_minutes": s.duration_minutes, "is_additional": s.is_additional,
                "description": (s.description or "")[:300]} for s in services],
                "possible_matches_need_confirmation": bool(args.query.strip())}
        if name == "get_service_details":
            data = self.service_data(await self.service(args.service_id))
            if not self.policy.show_prices:
                for key in ("price", "tariff_description"):
                    data.pop(key, None)
            return data
        if name == "get_prices":
            return await self.quote(args)
        if name == "get_specialists":
            employees = await self.specialists(await self.service(args.service_id))
            rules = {}
            if self.is_venue() and employees and self.policy.show_prices:
                rows = (await self.db.execute(select(VenueTimePricing).where(
                    VenueTimePricing.employee_id.in_([e.id for e in employees])))).scalars().all()
                for r in rows:
                    rules.setdefault(r.employee_id, []).append({
                        "from": r.time_from.strftime("%H:%M"), "to": r.time_to.strftime("%H:%M"),
                        "price_per_hour": r.price_per_hour, "days": r.day_type})
            return {"specialists": [{"id": e.id, "name": e.name, "specialization": e.specialization,
                "description": e.description, "instructions": e.ai_instruction, "capacity": e.capacity,
                **({"time_pricing": rules.get(e.id, []),
                    "solo_price_per_hour": e.solo_price_per_hour if self.policy.show_prices else None}
                   if self.is_venue() else {})} for e in employees]}
        if name == "check_availability":
            return await self.availability(args)
        if name == "get_my_bookings":
            if not self.user:
                return {"error": "Для просмотра личных записей войдите в ServGo"}
            rows = (await self.db.execute(select(Booking).where(
                Booking.business_id == self.business.id, Booking.client_id == self.user.id,
                Booking.booking_date >= Date.today()).order_by(Booking.booking_date, Booking.booking_time)
                .limit(10))).scalars().all()
            return {"bookings": [{"id": b.id, "date": str(b.booking_date),
                "time": b.booking_time.strftime("%H:%M"), "status": b.status,
                "manage_url": (f"/rental/b/{b.notify_token}" if b.duration_minutes is not None
                    else f"/b/{b.notify_token}") if b.notify_token else "/my"} for b in rows]}
        raise HTTPException(422, "Неизвестный инструмент")

    async def book(self, prepared: dict, confirmation) -> tuple[list[int], str | None]:
        args = BookingArgs.model_validate(prepared["args"])
        await lock_resource_days(self.db, args.employee_id, [args.date])
        fresh = await self.prepare_booking(args)
        if fresh["quote"] != prepared["quote"] or fresh["description"] != prepared["description"]:
            raise HTTPException(409, "Условия записи изменились. Запросите новую карточку подтверждения")
        name = self.user.full_name if self.user else confirmation.client_name
        phone = self.user.phone if self.user and self.user.phone else confirmation.client_phone
        if fresh["requires_contact"] and (not name or not phone):
            raise HTTPException(422, "Укажите имя и телефон в форме подтверждения")
        if phone:
            digits = "".join(c for c in phone if c.isdigit())
            if not 10 <= len(digits) <= 15:
                raise HTTPException(422, "Укажите корректный телефон")
            phone = "+" + digits
        if fresh["requires_legal_consent"] and not (
            confirmation.personal_data_consent and confirmation.booking_terms_accepted):
            raise HTTPException(422, "Отдельно подтвердите согласие на обработку данных и условия записи")
        # Площадки и обычные услуги — одна и та же запись, как со страницы записи.
        body = CreateBookingIn(business_id=self.business.id, service_id=args.service_id,
            employee_id=args.employee_id, date=args.date, time=args.time,
            additional_service_ids=args.additional_service_ids, client_name=name, client_phone=phone,
            is_solo_booking=args.solo,
            personal_data_consent=confirmation.personal_data_consent,
            booking_terms_accepted=confirmation.booking_terms_accepted)
        # Guest creation in the existing CRUD helper commits. Flush our guest here instead
        # so the booking and confirmation result remain one atomic transaction.
        user = self.user
        if user is None:
            import secrets
            from database.models import User
            user = User(telegram_id=-secrets.randbelow(2**60)-1, full_name=name, phone=phone)
            self.db.add(user)
            await self.db.flush()
        result = await client.create_booking_visit(body, self.db, user, commit=False)
        ids = [b.id for b in result]
        booking = await self.db.get(Booking, ids[0])
        return ids, f"/b/{booking.notify_token}" if booking.notify_token else None
