"""Авто точка (business 16): выходные мастеров и услуги — чтение / запись.

По умолчанию только читает и печатает состояние (без личных данных клиентов).
Запускается на боевом сервере в окружении приложения.
"""
import argparse
import asyncio
import json
from datetime import date, timedelta

BUSINESS_ID = 16


async def run(apply: bool) -> None:
    from sqlalchemy import select
    from database.database import async_session_maker
    from database.models import Business, Employee, EmployeeDayOff, EmployeeService, Service

    async with async_session_maker() as db:
        business = await db.get(Business, BUSINESS_ID)
        if not business:
            raise RuntimeError('Business 16 not found')
        print(f'business={business.id}; name={business.name}; type={business.business_type}; '
              f'hours={business.working_hours_start}-{business.working_hours_end}; interval={business.booking_interval_minutes}')
        employees = (await db.execute(select(Employee).where(Employee.business_id == BUSINESS_ID).order_by(Employee.id))).scalars().all()
        services = (await db.execute(select(Service).where(Service.business_id == BUSINESS_ID).order_by(Service.sort_order, Service.id))).scalars().all()
        links = (await db.execute(select(EmployeeService.employee_id, EmployeeService.service_id)
                                  .join(Employee, Employee.id == EmployeeService.employee_id)
                                  .where(Employee.business_id == BUSINESS_ID))).all()
        by_emp = {}
        for e_id, s_id in links:
            by_emp.setdefault(e_id, set()).add(s_id)
        print('--- employees')
        for e in employees:
            print(f'emp id={e.id}; name={e.name!r}; role={e.role}; active={e.is_active}; spec={e.specialization!r}; '
                  f'interval={e.booking_interval_minutes}; service_links={sorted(by_emp.get(e.id, []))}')
        print('--- services')
        for s in services:
            print(f'svc id={s.id}; name={s.name!r}; price={s.price}; min={s.duration_minutes}; active={s.is_active}; '
                  f'additional={s.is_additional}; sort={s.sort_order}; allowed_emp={s.available_employee_ids}')
        today = date.today()
        offs = (await db.execute(select(EmployeeDayOff).join(Employee, Employee.id == EmployeeDayOff.employee_id)
                                 .where(Employee.business_id == BUSINESS_ID, EmployeeDayOff.date >= today)
                                 .order_by(EmployeeDayOff.employee_id, EmployeeDayOff.date))).scalars().all()
        print(f'--- days_off_from_today={today}; count={len(offs)}')
        for o in offs:
            print(f'off emp={o.employee_id}; date={o.date}; reason={o.reason!r}')
        if apply:
            raise RuntimeError('apply is not implemented in this version')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    asyncio.run(run(parser.parse_args().apply))
