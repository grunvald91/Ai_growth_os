import unittest
from pathlib import Path


class ScheduleBusinessGuardTest(unittest.TestCase):
    def setUp(self):
        self.source = Path('web/api/routes/admin.py').read_text(encoding='utf-8')

    def test_duty_schedule_routes_filter_employee_by_business(self):
        self.assertIn(
            'select(Employee).where(Employee.id == employee_id, Employee.business_id == business_id)',
            self.source,
        )
        self.assertIn(
            'select(VenueTimePricing).join(Employee, VenueTimePricing.employee_id == Employee.id)',
            self.source,
        )
        self.assertIn('VenueTimePricing.employee_id == employee_id', self.source)
        self.assertIn('Employee.business_id == business_id', self.source)

    def test_employee_service_routes_do_not_leak_cross_business_data(self):
        self.assertIn(
            'select(EmployeeService).join(Employee, EmployeeService.employee_id == Employee.id)',
            self.source,
        )
        self.assertIn('EmployeeService.employee_id == employee_id', self.source)
        self.assertIn('Employee.business_id == business_id', self.source)


if __name__ == '__main__':
    unittest.main()
