import unittest

from email_utils import normalize_optional_email


class EmailUtilsTests(unittest.TestCase):
    def test_empty_email_is_optional(self):
        self.assertIsNone(normalize_optional_email(""))
        self.assertIsNone(normalize_optional_email("   "))
        self.assertIsNone(normalize_optional_email(None))

    def test_email_is_normalized(self):
        self.assertEqual(
            normalize_optional_email("  Arthur.Example@Example.COM "),
            "arthur.example@example.com",
        )

    def test_invalid_email_is_rejected(self):
        for value in ("abc", "@example.com", "a@b", "a b@example.com"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_optional_email(value)


if __name__ == "__main__":
    unittest.main()
