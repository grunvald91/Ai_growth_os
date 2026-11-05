import unittest
from datetime import date, time

from web.api.rental_logic import price_quote, available_starts, intervals_overlap


class RentalLogicTest(unittest.TestCase):
    def test_short_rental_requires_price_confirmation(self):
        self.assertEqual(price_quote(19000, 4, [15000]), (None, True, 15000))

    def test_five_hours_with_fixed_addons(self):
        self.assertEqual(price_quote(19000, 5, [15000, 5000]), (115000, False, 20000))

    def test_six_hours_without_addons(self):
        self.assertEqual(price_quote(16000, 6, []), (96000, False, 0))

    def test_overlap_catches_partial_and_contained_intervals(self):
        self.assertTrue(intervals_overlap(11 * 60, 16 * 60, 14 * 60, 15 * 60))
        self.assertTrue(intervals_overlap(14 * 60, 15 * 60, 11 * 60, 16 * 60))
        self.assertFalse(intervals_overlap(10 * 60, 11 * 60, 11 * 60, 16 * 60))

    def test_available_starts_respect_full_duration_and_hour_boundaries(self):
        starts = available_starts(date(2026, 10, 1), time(9), time(20), 5 * 60,
                                  [(time(10), 5 * 60)], now=None)
        self.assertEqual(starts, ['15:00'])

    def test_adjacent_booking_on_other_vessel_is_not_passed_to_its_slots(self):
        starts = available_starts(date(2026, 10, 1), time(9), time(20), 5 * 60,
                                  [], now=None)
        self.assertIn('10:00', starts)

    def test_no_past_slots_or_end_of_day_overflow(self):
        from datetime import datetime
        starts = available_starts(date(2026, 10, 1), time(9), time(20), 3 * 60,
                                  [], now=datetime(2026, 10, 1, 14, 10))
        self.assertEqual(starts, ['16:00', '17:00'])


if __name__ == '__main__':
    unittest.main()
