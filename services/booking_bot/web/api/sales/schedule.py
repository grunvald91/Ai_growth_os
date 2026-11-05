"""Additional resource restrictions shared by sales and rental validation."""
import json
from sqlalchemy import select
from database.models import EmployeeDayOff


async def resource_works(db, employee, day, duration):
    if employee.min_booking_minutes and duration < employee.min_booking_minutes:
        return False
    if employee.duty_schedule:
        try:
            dates = json.loads(employee.duty_schedule)
            if not isinstance(dates, list) or day.isoformat() not in dates:
                return False
        except (ValueError, TypeError):
            return False
    elif employee.on_duty_date and employee.on_duty_date != day:
        return False
    off = (await db.execute(select(EmployeeDayOff.id).where(
        EmployeeDayOff.employee_id == employee.id, EmployeeDayOff.date == day))).first()
    return off is None
