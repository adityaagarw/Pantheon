import unittest
from decimal import Decimal

from close.money import cents, fmt


class MoneyTest(unittest.TestCase):
    def test_cents_rounds_half_up(self):
        self.assertEqual(cents(Decimal("2.345")), Decimal("2.35"))
        self.assertEqual(cents(Decimal("-2.345")), Decimal("-2.35"))

    def test_fmt_has_two_decimals(self):
        self.assertEqual(fmt(Decimal("12")), "12.00")
        self.assertEqual(fmt(Decimal("-0.5")), "-0.50")


if __name__ == "__main__":
    unittest.main()
