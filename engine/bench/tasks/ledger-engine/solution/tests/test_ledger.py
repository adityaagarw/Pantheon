import textwrap
import unittest
from decimal import Decimal as D

from ledger.amounts import Amount, format_amount, parse_amount
from ledger.errors import JournalError
from ledger.parser import parse
from ledger.reports import balance


def j(text):
    return parse(textwrap.dedent(text).lstrip("\n"))


class Smoke(unittest.TestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("$-1,000.5"), Amount(D("-1000.5"), "$"))
        self.assertEqual(format_amount(Amount(D("-0.004"), "EUR"), 2), "0.00 EUR")

    def test_parse_and_balance(self):
        jr = j("""
            2025-01-01 Open
                assets:bank   $10.00
                equity
            """)
        self.assertEqual(balance(jr).as_dict()["assets"].get("$"), D("10.00"))

    def test_unbalanced(self):
        with self.assertRaises(JournalError):
            j("""
                2025-01-01 X
                    a   $1
                    b   $1
                """)


if __name__ == "__main__":
    unittest.main()
