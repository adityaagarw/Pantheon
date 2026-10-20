import json
import os
import re
import unittest
from pathlib import Path

WORKSPACE = Path(os.environ["SWARMBENCH_WORKSPACE"])
EXPECTED = json.loads((Path(__file__).parent / "expected_register.json").read_text(encoding="utf-8"))
FIELDS = ["vendor", "annual_fee_usd", "current_term_end", "auto_renews", "notice_deadline",
          "liability_cap_usd", "governing_law", "personal_data", "dpa",
          "termination_for_convenience", "renewal_price_cap_pct", "terminates_on", "violations",
          "action"]


def load_register():
    path = WORKSPACE / "register.json"
    if not path.is_file():
        raise AssertionError("register.json is missing")
    return json.loads(path.read_text(encoding="utf-8"))


def norm_law(value):
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"\b(the|laws?|of|state|federal|republic|commonwealth)\b", " ", text)
    return " ".join(re.sub(r"[^a-z ]", " ", text).split())


def same(field, got, want):
    if field in ("annual_fee_usd", "liability_cap_usd", "renewal_price_cap_pct"):
        if want is None or got is None:
            return got is want
        try:
            return abs(float(got) - float(want)) < 0.006
        except (TypeError, ValueError):
            return False
    if field == "governing_law":
        return norm_law(got) == norm_law(want)
    if field == "vendor":
        return str(want).lower().split()[0] in str(got).lower()
    if field == "violations":
        return isinstance(got, list) and sorted(got) == sorted(want)
    return got == want


class Register(unittest.TestCase):
    def test_review_date(self):
        self.assertEqual(load_register().get("review_date"), EXPECTED["review_date"])

    def test_listed_contracts(self):
        got = [c.get("contract_id") for c in load_register().get("contracts", [])]
        self.assertEqual(got, [c["contract_id"] for c in EXPECTED["contracts"]])

    def test_terminated_contract_left_out(self):
        got = {c.get("contract_id") for c in load_register().get("contracts", [])}
        self.assertNotIn("CT-109", got)


def make_test(cid, field, want):
    def test(self):
        rows = {c.get("contract_id"): c for c in load_register().get("contracts", [])}
        self.assertIn(cid, rows, f"{cid} is not in the register")
        self.assertIn(field, rows[cid], f"{cid} has no {field}")
        self.assertTrue(same(field, rows[cid][field], want),
                        f"{cid} {field}: got {rows[cid][field]!r}, expected {want!r}")
    return test


class Values(unittest.TestCase):
    pass


for row in EXPECTED["contracts"]:
    for field in FIELDS:
        name = f"test_{row['contract_id'].replace('-', '_')}_{field}"
        setattr(Values, name, make_test(row["contract_id"], field, row[field]))


class Memo(unittest.TestCase):
    def memo(self):
        path = WORKSPACE / "memo.md"
        if not path.is_file():
            raise AssertionError("memo.md is missing")
        return path.read_text(encoding="utf-8")

    def test_names_every_urgent_vendor(self):
        text = self.memo().lower()
        urgent = [c for c in EXPECTED["contracts"] if c["action"] in ("missed", "renegotiate_now")]
        self.assertTrue(urgent)
        for c in urgent:
            self.assertIn(c["vendor"].lower().split()[0], text, c["vendor"])

    def test_gives_dates_for_urgent_vendors(self):
        text = self.memo()
        for c in EXPECTED["contracts"]:
            if c["action"] == "renegotiate_now":
                self.assertIn(c["notice_deadline"] or c["current_term_end"], text)

    def test_total_spend_with_violations(self):
        total = sum(c["annual_fee_usd"] for c in EXPECTED["contracts"] if c["violations"])
        digits = re.sub(r"(?<=\d),(?=\d{3})", "", self.memo())
        whole = f"{total:.0f}"
        self.assertTrue(whole in digits or f"{total:.2f}" in digits,
                        f"the memo doesn't give the total {total:,.2f}")


if __name__ == "__main__":
    unittest.main()
