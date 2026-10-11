import tempfile
import unittest

from sales.pipeline import build_summary, parse_date, parse_price, parse_region

HEADER = "order_id,order_date,customer,region,product,quantity,unit_price,status\n"


class ParseTest(unittest.TestCase):
    def test_dates(self):
        self.assertEqual(str(parse_date("2026-7-3")), "2026-07-03")
        self.assertEqual(str(parse_date("03/07/2026")), "2026-07-03")
        self.assertEqual(str(parse_date("Jul 3, 2026")), "2026-07-03")
        self.assertIsNone(parse_date("2026-02-30"))
        self.assertIsNone(parse_date("07-03-2026"))

    def test_regions(self):
        self.assertEqual(parse_region("N. America"), "North America")
        self.assertEqual(parse_region("asia  pacific"), "Asia Pacific")
        self.assertIsNone(parse_region("Mars"))

    def test_prices(self):
        self.assertEqual(str(parse_price("$1,200.5")), "1200.5")
        self.assertIsNone(parse_price("12.505"))
        self.assertIsNone(parse_price("0"))


class SummaryTest(unittest.TestCase):
    def test_duplicates_and_revenue(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8") as f:
            f.write(HEADER + "A,2026-07-01,x,EU,W,1,10,completed\nA,2026-07-01,x,EU,W,2,10,completed\n")
        s = build_summary(f.name)
        self.assertEqual(s["duplicates_removed"], 1)
        self.assertEqual(s["total_revenue"], "20.00")


if __name__ == "__main__":
    unittest.main()
