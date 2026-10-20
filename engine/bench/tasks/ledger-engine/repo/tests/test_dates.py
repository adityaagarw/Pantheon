import unittest
from datetime import date

from ledger.dates import add_months, parse_date, period_start, periods, step


class Dates(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_date("2025-01-05"), date(2025, 1, 5))
        self.assertEqual(parse_date("2025/1/5"), date(2025, 1, 5))
        with self.assertRaises(ValueError):
            parse_date("5 Jan")

    def test_months_clamp(self):
        self.assertEqual(add_months(date(2025, 1, 31), 1), date(2025, 2, 28))
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))
        self.assertEqual(step(date(2025, 1, 31), "quarterly"), date(2025, 4, 30))

    def test_periods(self):
        self.assertEqual(period_start(date(2025, 5, 14), "quarterly"), date(2025, 4, 1))
        self.assertEqual(period_start(date(2025, 5, 14), "weekly"), date(2025, 5, 12))
        self.assertEqual(periods("monthly", date(2025, 1, 15), date(2025, 3, 1)),
                         [(date(2025, 1, 1), date(2025, 2, 1)), (date(2025, 2, 1), date(2025, 3, 1))])


if __name__ == "__main__":
    unittest.main()
