import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(WORKSPACE))

SIMPLE = {"exact", "alias"}
COMBINED = {"multi", "fee", "overpayment"}


def expected(month):
    return json.loads((HERE / f"expected-{month}.json").read_text(encoding="utf-8"))


class CloseMonth:
    month = None
    data = None
    close_date = None

    @classmethod
    def setUpClass(cls):
        from close.report import run

        cls.error = None
        try:
            cls.result = json.loads(json.dumps(run(cls.data, cls.close_date)))
        except Exception as e:  # noqa: BLE001 - every test reports it
            cls.result, cls.error = None, e
        cls.want = expected(cls.month)

    def got(self, key):
        if self.error is not None:
            self.fail(f"run() failed: {self.error!r}")
        self.assertIn(key, self.result)
        return self.result[key]

    def matches_of(self, result, kinds):
        return [m for m in result["matches"] if m["kind"] in kinds]

    def test_period_and_line_count(self):
        self.assertEqual(self.got("period_end"), self.want["period_end"])
        self.assertEqual(self.got("bank_lines"), self.want["bank_lines"])

    def test_simple_matches(self):
        self.got("matches")
        self.assertEqual(self.matches_of(self.result, SIMPLE), self.matches_of(self.want, SIMPLE))

    def test_combined_matches(self):
        self.got("matches")
        self.assertEqual(self.matches_of(self.result, COMBINED), self.matches_of(self.want, COMBINED))

    def test_partial_matches(self):
        self.got("matches")
        self.assertEqual(self.matches_of(self.result, {"partial"}),
                         self.matches_of(self.want, {"partial"}))

    def test_matching_order(self):
        self.assertEqual([m["line"] for m in self.got("matches")],
                         [m["line"] for m in self.want["matches"]])

    def test_bank_charges(self):
        self.assertEqual(self.got("bank_charges"), self.want["bank_charges"])

    def test_unmatched(self):
        self.assertEqual(self.got("unmatched"), self.want["unmatched"])

    def test_overpayments(self):
        self.assertEqual(self.got("overpayments"), self.want["overpayments"])

    def test_open_items(self):
        self.assertEqual(self.got("open_items"), self.want["open_items"])

    def test_ar_aging(self):
        self.assertEqual(self.got("ar_aging_usd"), self.want["ar_aging_usd"])

    def test_ap_aging(self):
        self.assertEqual(self.got("ap_aging_usd"), self.want["ap_aging_usd"])

    def test_fees(self):
        self.assertEqual(self.got("fees_usd"), self.want["fees_usd"])

    def test_fx_gain_loss(self):
        self.assertEqual(self.got("fx_gain_loss_usd"), self.want["fx_gain_loss_usd"])

    def test_cash(self):
        self.assertEqual(self.got("cash"), self.want["cash"])
        self.assertEqual(self.got("cash_usd_total"), self.want["cash_usd_total"])


class March(CloseMonth, unittest.TestCase):
    month = "2026-03"
    data = WORKSPACE / "data" / "2026-03"
    close_date = date(2026, 3, 31)


class April(CloseMonth, unittest.TestCase):
    month = "2026-04"
    data = HERE / "data" / "2026-04"
    close_date = date(2026, 4, 30)


class Loaders(unittest.TestCase):
    def test_northwind(self):
        from close.banks import load_northwind

        lines = load_northwind(HERE / "data" / "edge" / "bank_northwind.csv")
        self.assertEqual(len(lines), 2)
        first, second = lines
        self.assertEqual(first["id"], "northwind:1")
        self.assertEqual(first["bank"], "northwind")
        self.assertEqual(first["date"], date(2026, 3, 31))
        self.assertEqual(first["description"], "Wire, from client INV-1")
        self.assertEqual(first["amount"], Decimal("-1000.00"))
        self.assertIsInstance(first["amount"], Decimal)
        self.assertEqual(first["balance"], Decimal("2000.00"))
        self.assertEqual(first["currency"], "USD")
        self.assertEqual((second["id"], second["amount"]), ("northwind:2", Decimal("3000.00")))

    def test_eurobank(self):
        from close.banks import load_eurobank

        lines = load_eurobank(HERE / "data" / "edge" / "bank_eurobank.csv")
        self.assertEqual([l["id"] for l in lines], ["eurobank:1", "eurobank:2"])
        self.assertEqual(lines[0]["description"], "Zahlung; INV-7")
        self.assertEqual(lines[0]["amount"], Decimal("1234567.89"))
        self.assertEqual(lines[0]["date"], date(2026, 3, 1))
        self.assertEqual(lines[1]["description"], "Gebuehr")
        self.assertEqual(lines[1]["amount"], Decimal("-0.50"))
        self.assertEqual(lines[1]["currency"], "EUR")
        self.assertNotIn("balance", lines[1])


class Rates(unittest.TestCase):
    def setUp(self):
        from close.fx import Rates as R

        self.rates = R.load(HERE / "data" / "edge" / "fx.csv")

    def test_rate_carries_forward(self):
        self.assertEqual(self.rates.rate(date(2026, 3, 4)), Decimal("2.1650"))
        self.assertEqual(self.rates.rate(date(2026, 3, 9)), Decimal("1.0815"))

    def test_no_rate_before_the_first_day(self):
        with self.assertRaises(ValueError):
            self.rates.rate(date(2026, 3, 1))

    def test_conversion_rounds_half_up(self):
        self.assertEqual(self.rates.to_usd(Decimal("5"), "EUR", date(2026, 3, 4)), Decimal("10.83"))
        self.assertEqual(self.rates.to_usd(Decimal("12.34"), "USD", date(2026, 3, 4)), Decimal("12.34"))


class Command(unittest.TestCase):
    def test_writes_the_close_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(WORKSPACE)}
            proc = subprocess.run([sys.executable, "-m", "close", str(HERE / "data" / "2026-04"),
                                   "2026-04-30"], cwd=tmp, env=env, capture_output=True, text=True,
                                  timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
            out = Path(tmp) / "out" / "close-2026-04.json"
            self.assertTrue(out.is_file(), "out/close-2026-04.json is missing")
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), expected("2026-04"))

    def test_march_close_file_in_the_work_folder(self):
        out = WORKSPACE / "out" / "close-2026-03.json"
        self.assertTrue(out.is_file(), "out/close-2026-03.json is missing")
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")), expected("2026-03"))


class Balance(unittest.TestCase):
    def test_a_statement_that_does_not_add_up_is_refused(self):
        from close.report import run

        with self.assertRaises(ValueError):
            run(HERE / "data" / "broken", date(2026, 3, 31))


if __name__ == "__main__":
    unittest.main()
