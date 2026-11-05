import unittest
from datetime import date, timedelta

from web.api.booking_utils import compute_recurring_dates


class ComputeRecurringDatesTest(unittest.TestCase):
    def test_single_week_returns_only_start_date(self):
        self.assertEqual(compute_recurring_dates(date(2026, 8, 12), 1), [date(2026, 8, 12)])

    def test_multiple_weeks_are_spaced_seven_days_apart(self):
        result = compute_recurring_dates(date(2026, 8, 12), 4)
        self.assertEqual(result, [
            date(2026, 8, 12),
            date(2026, 8, 19),
            date(2026, 8, 26),
            date(2026, 9, 2),
        ])

    def test_eight_weeks_is_the_max_allowed(self):
        result = compute_recurring_dates(date(2026, 1, 1), 8)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[-1], date(2026, 1, 1) + timedelta(weeks=7))

    def test_zero_weeks_raises(self):
        with self.assertRaises(ValueError):
            compute_recurring_dates(date(2026, 1, 1), 0)

    def test_nine_weeks_raises(self):
        with self.assertRaises(ValueError):
            compute_recurring_dates(date(2026, 1, 1), 9)


if __name__ == '__main__':
    unittest.main()
