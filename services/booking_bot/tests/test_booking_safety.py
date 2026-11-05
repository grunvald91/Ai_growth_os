from datetime import datetime
import unittest

from booking_intervals import intervals_overlap


class BookingSafetyUnitTests(unittest.TestCase):
    def test_same_interval_overlaps(self):
        start = datetime(2026, 10, 6, 10, 0)
        end = datetime(2026, 10, 6, 11, 0)
        self.assertTrue(intervals_overlap(start, end, start, end))

    def test_partial_overlap_is_rejected(self):
        self.assertTrue(
            intervals_overlap(
                datetime(2026, 10, 6, 10, 30),
                datetime(2026, 10, 6, 11, 30),
                datetime(2026, 10, 6, 10, 0),
                datetime(2026, 10, 6, 11, 0),
            )
        )

    def test_contained_interval_overlaps(self):
        self.assertTrue(
            intervals_overlap(
                datetime(2026, 10, 6, 10, 15),
                datetime(2026, 10, 6, 10, 45),
                datetime(2026, 10, 6, 10, 0),
                datetime(2026, 10, 6, 11, 0),
            )
        )

    def test_touching_intervals_do_not_overlap(self):
        self.assertFalse(
            intervals_overlap(
                datetime(2026, 10, 6, 11, 0),
                datetime(2026, 10, 6, 12, 0),
                datetime(2026, 10, 6, 10, 0),
                datetime(2026, 10, 6, 11, 0),
            )
        )

    def test_cross_midnight_overlap(self):
        self.assertTrue(
            intervals_overlap(
                datetime(2026, 10, 7, 0, 15),
                datetime(2026, 10, 7, 1, 0),
                datetime(2026, 10, 6, 23, 30),
                datetime(2026, 10, 7, 0, 30),
            )
        )


if __name__ == "__main__":
    unittest.main()
