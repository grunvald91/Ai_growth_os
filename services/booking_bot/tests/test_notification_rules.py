import unittest

from notification_rules import (
    due_reminder_offset,
    normalize_reminder_minutes,
)


class NotificationRulesTests(unittest.TestCase):
    def test_defaults_are_24h_and_2h(self):
        self.assertEqual(normalize_reminder_minutes(None), [1440, 120])

    def test_normalization_deduplicates_sorts_and_filters(self):
        self.assertEqual(
            normalize_reminder_minutes([30, 1440, 120, 30, 4, 10081, "60"]),
            [1440, 120, 60, 30],
        )

    def test_api_can_reject_empty_after_filtering(self):
        self.assertEqual(
            normalize_reminder_minutes([0, -1, 20000], fallback=None),
            [],
        )

    def test_due_threshold_does_not_send_all_missed_reminders(self):
        offsets = [1440, 120, 30]
        self.assertEqual(due_reminder_offset(90, offsets), 120)
        self.assertEqual(due_reminder_offset(20, offsets), 30)

    def test_not_due_before_largest_threshold(self):
        self.assertIsNone(due_reminder_offset(1500, [1440, 120]))

    def test_past_visit_has_no_due_reminder(self):
        self.assertIsNone(due_reminder_offset(0, [1440, 120]))
        self.assertIsNone(due_reminder_offset(-5, [1440, 120]))


if __name__ == "__main__":
    unittest.main()
