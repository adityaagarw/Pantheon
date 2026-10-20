import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from close.banks import load_eurobank, load_northwind
from close.ledger import bucket
from close.matching import match
from close.report import run

DATA = Path(__file__).resolve().parent.parent / "data" / "2026-03"


def line(n, amount, description, day=date(2026, 3, 10), currency="USD"):
    bank = "northwind" if currency == "USD" else "eurobank"
    return {"id": f"{bank}:{n}", "bank": bank, "date": day, "description": description,
            "amount": Decimal(amount), "currency": currency}


def item(iid, amount, cp="Acme", kind="invoice", currency="USD", due=date(2026, 3, 1)):
    return {"id": iid, "kind": kind, "counterparty": cp, "currency": currency,
            "amount": Decimal(amount), "open": Decimal(amount), "issued": date(2026, 2, 1),
            "due": due}


class Banks(unittest.TestCase):
    def test_both_statements_load(self):
        nw = load_northwind(DATA / "bank_northwind.csv")
        eb = load_eurobank(DATA / "bank_eurobank.csv")
        self.assertTrue(nw and eb)
        self.assertEqual(nw[0]["id"], "northwind:1")
        self.assertGreaterEqual(nw[0]["date"], nw[-1]["date"])  # newest first
        self.assertTrue(all(isinstance(l["amount"], Decimal) for l in nw + eb))


class Matching(unittest.TestCase):
    def test_fee_up_to_25(self):
        items = [item("INV-1", "100.00")]
        out = match([line(1, "75.00", "INV-1")], items, {})
        self.assertEqual(out["matches"][0]["kind"], "fee")
        items = [item("INV-1", "100.00")]
        out = match([line(1, "74.99", "INV-1")], items, {})
        self.assertEqual(out["matches"][0]["kind"], "partial")

    def test_partial_goes_by_due_date(self):
        items = [item("INV-1", "100.00", due=date(2026, 3, 9)), item("INV-2", "100.00", due=date(2026, 2, 1))]
        out = match([line(1, "150.00", "INV-1 INV-2")], items, {})
        self.assertEqual(out["matches"][0]["items"], [("INV-2", Decimal("100.00")), ("INV-1", Decimal("50.00"))])

    def test_ambiguous_alias(self):
        items = [item("INV-1", "50.00"), item("INV-2", "50.00")]
        out = match([line(1, "50.00", "ACME PAYMENT")], items, {"Acme": ["ACME"]})
        self.assertEqual(out["unmatched"][0]["reason"], "ambiguous")

    def test_overpayment(self):
        items = [item("INV-1", "50.00")]
        out = match([line(1, "60.00", "inv-1")], items, {})
        self.assertEqual(out["overpayments"][0]["amount"], Decimal("10.00"))


class Aging(unittest.TestCase):
    def test_buckets(self):
        close = date(2026, 3, 31)
        self.assertEqual(bucket(date(2026, 3, 31), close), "current")
        self.assertEqual(bucket(date(2026, 3, 1), close), "1-30")
        self.assertEqual(bucket(date(2026, 1, 30), close), "31-60")
        self.assertEqual(bucket(date(2026, 1, 29), close), "61-90")
        self.assertEqual(bucket(date(2025, 12, 31), close), "61-90")
        self.assertEqual(bucket(date(2025, 12, 30), close), "90+")


class Report(unittest.TestCase):
    def test_march_closes(self):
        result = run(DATA, date(2026, 3, 31))
        self.assertEqual(result["period_end"], "2026-03-31")
        self.assertEqual(set(result["ar_aging_usd"]), {"current", "1-30", "31-60", "61-90", "90+"})


if __name__ == "__main__":
    unittest.main()
