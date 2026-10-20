import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal as D
from pathlib import Path

from ledger.amounts import Amount, MixedAmount, format_amount, parse_amount
from ledger.budget import budget_report
from ledger.cli import main
from ledger.errors import JournalError, PriceError
from ledger.parser import load, parse
from ledger.prices import PriceDB
from ledger.query import Query
from ledger.reports import balance, balance_by_period, income_statement, register


def j(text, **kw):
    return parse(textwrap.dedent(text).lstrip("\n"), **kw)


def M(*pairs):
    return MixedAmount([Amount(D(q), c) for q, c in pairs])


def rows(report):
    return {r.account: r.amount for r in report.rows}


def files_loader(files):
    norm = {os.path.normpath(k): v for k, v in files.items()}

    def load_(path):
        key = os.path.normpath(path)
        if key not in norm:
            raise FileNotFoundError(path)
        return norm[key]
    return load_


BASIC = """
2025-01-01 * Opening
    assets:bank:checking   $1,000.00
    assets:cash              $50.00
    equity:opening

2025-01-03 Grocer
    expenses:food:groceries   $30.25
    assets:cash

2025-01-15 * Employer | January pay
    assets:bank:checking   $2,000.00
    income:salary

2025-02-01 * (77) Landlord | rent
    expenses:rent   $900.00
    assets:bank:checking
"""


class Amounts(unittest.TestCase):
    def test_symbol_forms(self):
        self.assertEqual(parse_amount("$1,234.56"), Amount(D("1234.56"), "$"))
        self.assertEqual(parse_amount("-$5"), Amount(D("-5"), "$"))
        self.assertEqual(parse_amount("$-5.10"), Amount(D("-5.10"), "$"))
        self.assertEqual(parse_amount("$ 7"), Amount(D("7"), "$"))
        self.assertEqual(parse_amount("10€"), Amount(D("10"), "€"))

    def test_code_forms(self):
        self.assertEqual(parse_amount("1,000.5 EUR"), Amount(D("1000.5"), "EUR"))
        self.assertEqual(parse_amount("EUR -3"), Amount(D("-3"), "EUR"))
        self.assertEqual(parse_amount("-12 AAPL"), Amount(D("-12"), "AAPL"))
        self.assertEqual(parse_amount("3 hours"), Amount(D("3"), "hours"))
        self.assertEqual(parse_amount("42"), Amount(D("42"), ""))

    def test_bad_amounts(self):
        for bad in ("12,34", "-$-5", "+5", ".5", "$", "1,2345.00", "5 EUR USD"):
            with self.assertRaises(ValueError, msg=bad):
                parse_amount(bad)

    def test_format(self):
        self.assertEqual(format_amount(Amount(D("1234.5"), "$"), 2), "$1,234.50")
        self.assertEqual(format_amount(Amount(D("-1234.5"), "EUR"), 2), "-1,234.50 EUR")
        self.assertEqual(format_amount(Amount(D("1234567"), ""), 0), "1,234,567")
        self.assertEqual(format_amount(Amount(D("0.125"), "BTC"), 3), "0.125 BTC")

    def test_rounding_half_away_from_zero_and_no_negative_zero(self):
        self.assertEqual(format_amount(Amount(D("0.005"), "$"), 2), "$0.01")
        self.assertEqual(format_amount(Amount(D("-0.005"), "$"), 2), "-$0.01")
        self.assertEqual(format_amount(Amount(D("-0.004"), "$"), 2), "$0.00")
        self.assertEqual(format_amount(Amount(D("2.5"), "JPY"), 0), "3 JPY")

    def test_mixed_arithmetic(self):
        m = M(("10", "$"), ("5", "EUR")) + Amount(D("-10"), "$")
        self.assertEqual(m.amounts(), [Amount(D("5"), "EUR")])
        self.assertEqual(m.get("$"), D("0"))
        self.assertEqual(-m, M(("-5", "EUR")))
        self.assertEqual(m - M(("5", "EUR")), MixedAmount())

    def test_mixed_zero_and_lines(self):
        m = M(("0.001", "$"), ("2", "EUR"), ("1", "AAPL"))
        self.assertEqual(m.lines({"$": 2, "EUR": 2, "AAPL": 0}), ["1 AAPL", "2.00 EUR"])
        self.assertTrue(M(("0.004", "$")).is_zero({"$": 2}))
        self.assertFalse(M(("0.004", "$")).is_zero({"$": 3}))
        self.assertEqual(MixedAmount().lines({}), ["0"])
        self.assertEqual(M(("1.234", "X")).lines({}), ["1.23 X"])


class Parsing(unittest.TestCase):
    def test_transaction_fields(self):
        jr = j("""
            2025/1/5 * (INV-1042) ACME Corp | March invoice ; billed:yes, client:acme
                assets:receivable   $120
                income:consulting
            """)
        t = jr.transactions[0]
        self.assertEqual((t.date, t.status, t.code), (date(2025, 1, 5), "*", "INV-1042"))
        self.assertEqual((t.payee, t.note), ("ACME Corp", "March invoice"))
        self.assertEqual(t.description, "ACME Corp | March invoice")
        self.assertEqual(t.comment, "billed:yes, client:acme")
        self.assertEqual(t.tags, {"billed": "yes", "client": "acme"})
        self.assertEqual(t.line, 1)
        self.assertEqual(t.postings[1].amount, Amount(D("-120"), "$"))

    def test_comment_lines_join(self):
        jr = j("""
            2025-01-05 Note ; first
                ; second
                assets:a   $1 ; on a
                ; more on a
                assets:b
            """)
        t = jr.transactions[0]
        self.assertEqual(t.comment, "first\nsecond")
        self.assertEqual(t.postings[0].comment, "on a\nmore on a")

    def test_account_names_and_separators(self):
        jr = j("""
            2025-01-05 Cafe
                expenses:eating out   $4.50
                ! assets:cash\t-$4.50 ; paid:cash
            """)
        p1, p2 = jr.transactions[0].postings
        self.assertEqual(p1.account, "expenses:eating out")
        self.assertEqual((p2.account, p2.status, p2.amount), ("assets:cash", "!", Amount(D("-4.50"), "$")))
        self.assertEqual(p2.tags, {"paid": "cash"})
        self.assertEqual(p2.line, 3)

    def test_posting_tags_inherit_and_override(self):
        jr = j("""
            2025-01-05 Trip ; trip:berlin, reimbursable:
                expenses:travel   $100 ; trip:munich
                assets:cash
            """)
        p1, p2 = jr.transactions[0].postings
        self.assertEqual(p1.tags, {"trip": "munich", "reimbursable": ""})
        self.assertEqual(p2.tags, {"trip": "berlin", "reimbursable": ""})

    def test_inferred_amount_multi_commodity(self):
        jr = j("""
            2025-01-05 Mixed
                assets:wallet:usd   $10
                assets:wallet:eur   5 EUR
                equity:swap
            """)
        ps = jr.transactions[0].postings
        self.assertEqual([(p.account, p.amount) for p in ps[2:]],
                         [("equity:swap", Amount(D("-10"), "$")), ("equity:swap", Amount(D("-5"), "EUR"))])

    def test_inferred_zero(self):
        jr = j("""
            2025-01-05 Nothing
                assets:a   $5
                assets:b   -$5
                assets:c
            """)
        self.assertEqual(jr.transactions[0].postings[2].amount.quantity, D("0"))

    def test_costs(self):
        jr = j("""
            2025-03-01 Buy
                assets:broker   10 AAPL @ $150.00
                assets:bank
            2025-03-02 Sell
                assets:broker   -4 AAPL @@ $640
                assets:bank      $640
            """)
        buy, sell = jr.transactions
        self.assertEqual(buy.postings[0].cost, Amount(D("1500.00"), "$"))
        self.assertEqual(buy.postings[1].amount, Amount(D("-1500.00"), "$"))
        self.assertEqual(sell.postings[0].cost, Amount(D("-640"), "$"))

    def test_virtual_postings_dont_balance(self):
        jr = j("""
            2025-01-05 Rent
                expenses:rent   $900
                assets:bank
                (budget:rent)   -$900
            """)
        ps = jr.transactions[0].postings
        self.assertTrue(ps[2].virtual)
        self.assertEqual(ps[2].account, "budget:rent")
        self.assertEqual(ps[1].amount, Amount(D("-900"), "$"))

    def test_virtual_needs_amount_and_brackets_refused(self):
        with self.assertRaises(JournalError):
            j("""
                2025-01-05 X
                    a   $1
                    b
                    (c)
                """)
        with self.assertRaises(JournalError):
            j("""
                2025-01-05 X
                    a   $1
                    [b]   -$1
                """)

    def test_unbalanced_is_an_error_at_the_transaction(self):
        with self.assertRaises(JournalError) as cm:
            j("""
                2025-01-01 Fine
                    a   $1
                    b

                2025-01-02 Off by a cent
                    a   $1.00
                    b   -$0.99
                """, path="books.journal")
        e = cm.exception
        self.assertEqual((e.line, e.path), (5, "books.journal"))
        self.assertEqual(str(e), "books.journal:5: transaction does not balance")

    THIRDS = """
        2025-01-01 Split three ways
            expenses:a   $3.333
            expenses:b   $3.333
            expenses:c   $3.333
            assets:cash  -$10.00
        """

    def test_balancing_uses_journal_wide_precision(self):
        # Written with 3 decimals, $ shows 3: off by $0.001, unbalanced.
        with self.assertRaises(JournalError):
            j(self.THIRDS)
        # A commodity directive anywhere in the journal sets 2: balanced.
        jr = j(self.THIRDS + "commodity $1,000.00\n")
        self.assertEqual(jr.precision("$"), 2)

    def test_rounding_within_precision_balances(self):
        jr = j("""
            commodity $1,000.00
            2025-01-01 Thirds
                expenses:a   $3.333
                expenses:b   $3.333
                expenses:c   $3.333
                assets:cash  -$10.00
            """)
        self.assertEqual(len(jr.transactions), 1)

    def test_two_blank_postings(self):
        with self.assertRaises(JournalError) as cm:
            j("""
                2025-01-01 X
                    a   $1
                    b
                    c
                """)
        self.assertIn("more than one posting without an amount", str(cm.exception))

    def test_no_postings_and_stray_lines(self):
        with self.assertRaises(JournalError) as cm:
            j("2025-01-01 Nothing here\n")
        self.assertIn("transaction has no postings", str(cm.exception))
        with self.assertRaises(JournalError) as cm:
            parse("    assets:a   $1\n")
        self.assertIn("posting outside a transaction", str(cm.exception))
        with self.assertRaises(JournalError) as cm:
            j("2025-01-01 X\n    a  $1\n    b\nbogus line\n")
        self.assertEqual(cm.exception.line, 4)
        self.assertIn("unexpected line", str(cm.exception))

    def test_comment_line_ends_transaction(self):
        with self.assertRaises(JournalError) as cm:
            j("2025-01-01 X\n    a  $1\n    b\n; a note\n    c  $2\n")
        self.assertIn("posting outside a transaction", str(cm.exception))

    def test_journal_order_is_by_date_then_file_order(self):
        jr = j("""
            2025-01-03 C
                a   $1
                b
            2025-01-01 A
                a   $1
                b
            2025-01-03 D
                a   $1
                b
            2025-01-02 B
                a   $1
                b
            """)
        self.assertEqual([t.payee for t in jr.transactions], ["A", "B", "C", "D"])


class Precision(unittest.TestCase):
    def test_inferred_from_written_amounts(self):
        jr = j("""
            P 2025-01-01 BTC $40,000.125
            2025-01-01 Buy
                assets:btc   0.5 BTC @ $40,000
                assets:bank  -$20,000.0
            2025-01-02 Fee
                expenses:fee   2 EUR
                assets:bank
            """)
        self.assertEqual(jr.precision("$"), 3)
        self.assertEqual(jr.precision("BTC"), 1)
        self.assertEqual(jr.precision("EUR"), 0)
        self.assertEqual(jr.format(Amount(D("1234.5"), "$")), "$1,234.500")

    def test_directive_wins(self):
        jr = j("""
            commodity 1,000.0000 BTC
            commodity $1,000
            2025-01-01 Buy
                assets:btc   0.5 BTC @ $40,000.25
                assets:bank
            """)
        self.assertEqual(jr.precision("BTC"), 4)
        self.assertEqual(jr.precision("$"), 0)
        self.assertEqual(jr.precision("GBP"), 2)

    def test_commodity_without_example(self):
        jr = j("commodity EUR\n2025-01-01 X\n    a   5.5 EUR\n    b\n")
        self.assertEqual(jr.precision("EUR"), 1)


class Assertions(unittest.TestCase):
    def test_passes_in_date_order(self):
        jr = j("""
            2025-01-05 Second
                assets:bank   $-20 = $80
                expenses:x
            2025-01-01 First
                assets:bank   $100 = $100
                equity
            """)
        self.assertEqual(len(jr.transactions), 2)

    def test_failure_message_and_line(self):
        with self.assertRaises(JournalError) as cm:
            j("""
                2025-01-01 Open
                    assets:bank   $100.00
                    equity
                2025-01-02 Spend
                    expenses:x   $30.00
                    assets:bank   = $75.00
                """, path="b.journal")
        e = cm.exception
        self.assertEqual(e.line, 6)
        self.assertEqual(e.message, "balance assertion failed for assets:bank: expected $75.00, got $70.00")

    def test_only_that_account_and_commodity(self):
        jr = j("""
            2025-01-01 Open
                assets:bank          $100
                assets:bank:savings  $50
                assets:bank          5 EUR
                equity
            2025-01-02 Check
                assets:bank   $0 = $100
                equity
            """)
        self.assertEqual(len(jr.transactions), 2)

    def test_virtual_postings_count(self):
        with self.assertRaises(JournalError):
            j("""
                2025-01-01 Open
                    (assets:bank)   $10
                2025-01-02 Check
                    assets:bank   $0 = $0
                    equity
                """)


class Directives(unittest.TestCase):
    def test_include_relative_and_errors_name_the_file(self):
        files = {
            "books/main.journal": "2025-01-01 Main\n    a   $1\n    b\ninclude sub/more.journal\n",
            "books/sub/more.journal": "2025-01-02 More\n    a   $2\n    b\ninclude last.journal\n",
            "books/sub/last.journal": "2025-01-03 Last\n    a   $3\n    b   -$2\n",
        }
        with self.assertRaises(JournalError) as cm:
            parse(files["books/main.journal"], path="books/main.journal", loader=files_loader(files))
        self.assertEqual(Path(cm.exception.path).name, "last.journal")
        self.assertEqual(cm.exception.line, 1)
        files["books/sub/last.journal"] = "2025-01-03 Last\n    a   $3\n    b\n"
        jr = parse(files["books/main.journal"], path="books/main.journal", loader=files_loader(files))
        self.assertEqual([t.payee for t in jr.transactions], ["Main", "More", "Last"])

    def test_include_cycle(self):
        files = {"a.journal": "include b.journal\n", "b.journal": "include a.journal\n"}
        with self.assertRaises(JournalError) as cm:
            parse(files["a.journal"], path="a.journal", loader=files_loader(files))
        self.assertIn("include cycle", str(cm.exception))

    def test_include_position_in_order(self):
        files = {"m.journal": "2025-01-01 Before\n    a  $1\n    b\ninclude x.journal\n"
                              "2025-01-01 After\n    a  $1\n    b\n",
                 "x.journal": "2025-01-01 Included\n    a  $1\n    b\n"}
        jr = parse(files["m.journal"], path="m.journal", loader=files_loader(files))
        self.assertEqual([t.payee for t in jr.transactions], ["Before", "Included", "After"])

    def test_aliases(self):
        jr = j("""
            alias bank = assets:bank:checking
            alias assets:bank:checking:fees = expenses:bank fees
            2025-01-01 X
                bank        $10
                bankx       $1
                bank:fees   $2
                equity
            end aliases
            2025-01-02 Y
                bank   $1
                equity
            """)
        accts = [p.account for p in jr.transactions[0].postings]
        self.assertEqual(accts, ["assets:bank:checking", "bankx", "expenses:bank fees", "equity"])
        self.assertEqual(jr.transactions[1].postings[0].account, "bank")

    def test_alias_applies_to_included_files(self):
        files = {"m.journal": "alias cash = assets:cash\ninclude x.journal\n",
                 "x.journal": "2025-01-01 X\n    cash  $1\n    equity\n"}
        jr = parse(files["m.journal"], path="m.journal", loader=files_loader(files))
        self.assertEqual(jr.transactions[0].postings[0].account, "assets:cash")

    def test_prices_list_in_file_order(self):
        jr = j("""
            P 2025-02-01 EUR $1.10
            2025-01-15 Buy
                assets:eur   100 EUR @@ $105
                assets:bank
            P 2025-01-01 EUR $1.00
            """)
        got = [(p.date, p.commodity, p.price) for p in jr.prices]
        self.assertEqual(got, [(date(2025, 2, 1), "EUR", Amount(D("1.10"), "$")),
                               (date(2025, 1, 15), "EUR", Amount(D("1.05"), "$")),
                               (date(2025, 1, 1), "EUR", Amount(D("1.00"), "$"))])

    def test_account_directive_and_unknown(self):
        j("account assets:bank\n")
        with self.assertRaises(JournalError):
            j("budget assets:bank\n")

    def test_periodic(self):
        jr = j("""
            ~ monthly from 2025-01-15  Budget
                expenses:food   $400
                assets:bank
            ~ weekly
                expenses:coffee   $20
                assets:bank
            2025-01-01 X
                a  $1
                b
            """)
        self.assertEqual(len(jr.transactions), 1)
        p1, p2 = jr.periodic
        self.assertEqual((p1.interval, p1.start, p1.description), ("monthly", date(2025, 1, 15), "Budget"))
        self.assertEqual((p2.interval, p2.start), ("weekly", None))
        self.assertEqual(p1.postings[1].amount, Amount(D("-400"), "$"))
        with self.assertRaises(JournalError) as cm:
            j("~ fortnightly\n    a  $1\n    b\n")
        self.assertIn("bad period", str(cm.exception))


class Prices(unittest.TestCase):
    JR = """
        P 2025-01-01 EUR $1.00
        P 2025-02-01 EUR $1.20
        P 2025-02-01 EUR $1.25
        P 2025-03-01 $ 0.80 EUR
        P 2025-03-01 EUR $1.30
        P 2025-04-01 GBP 1.2 EUR
        2025-01-01 X
            a  $1
            b
        """

    def test_latest_on_or_before(self):
        db = PriceDB(j(self.JR))
        self.assertEqual(db.rate("EUR", "$", date(2025, 1, 31)), D("1.00"))
        self.assertEqual(db.rate("EUR", "$", date(2025, 2, 1)), D("1.25"))
        self.assertEqual(db.rate("EUR", "EUR", date(2020, 1, 1)), D("1"))

    def test_direct_wins_a_tie_and_inverse_works(self):
        db = PriceDB(j(self.JR))
        self.assertEqual(db.rate("EUR", "$", date(2025, 3, 1)), D("1.30"))
        self.assertEqual(db.rate("$", "EUR", date(2025, 3, 1)), D("0.80"))
        self.assertEqual(round(db.rate("$", "EUR", date(2025, 2, 15)), 6), D("0.800000"))

    def test_no_price_and_no_chaining(self):
        db = PriceDB(j(self.JR))
        with self.assertRaises(PriceError):
            db.rate("EUR", "$", date(2024, 12, 31))
        with self.assertRaises(PriceError):
            db.rate("GBP", "$", date(2025, 5, 1))

    def test_value_leaves_unconvertible(self):
        db = PriceDB(j(self.JR))
        v = db.value(M(("10", "EUR"), ("3", "GBP"), ("1", "$")), "$", date(2025, 2, 10))
        self.assertEqual(v, M(("13.5", "$"), ("3", "GBP")))

    def test_cost_prices_count(self):
        jr = j("""
            2025-01-15 Buy
                assets:broker   10 AAPL @ $150
                assets:bank
            """)
        self.assertEqual(PriceDB(jr).rate("AAPL", "$", date(2025, 1, 15)), D("150"))


class Queries(unittest.TestCase):
    def setUp(self):
        self.jr = j("""
            2025-01-01 Grocer ; shop:local
                expenses:Food:groceries   $30
                assets:cash
            2025-01-02 Cafe Bleu
                expenses:food:coffee   $4 ; shop:chain
                assets:card
            2025-01-03 Hardware store
                expenses:home   $50
                assets:card
            """)
        self.ps = [p for t in self.jr.transactions for p in t.postings]

    def names(self, *terms):
        q = Query(list(terms))
        return [p.account for p in self.ps if q.matches(p)]

    def test_account_terms_are_or_and_case_insensitive(self):
        self.assertEqual(self.names("food"), ["expenses:Food:groceries", "expenses:food:coffee"])
        self.assertEqual(self.names("home", "cash"), ["assets:cash", "expenses:home"])
        self.assertEqual(len(self.names()), 6)

    def test_desc_tag_not(self):
        self.assertEqual(self.names("desc:^cafe"), ["expenses:food:coffee", "assets:card"])
        self.assertEqual(self.names("tag:shop"), ["expenses:Food:groceries", "assets:cash", "expenses:food:coffee"])
        self.assertEqual(self.names("tag:shop=loc"), ["expenses:Food:groceries", "assets:cash"])
        self.assertEqual(self.names("expenses", "not:food"), ["expenses:home"])
        self.assertEqual(self.names("card", "not:desc:hardware"), ["assets:card"])


class Balance(unittest.TestCase):
    def test_tree_inclusive_with_parents(self):
        rep = balance(j(BASIC))
        got = {k: v for k, v in rows(rep).items()}
        self.assertEqual(list(got), ["assets", "assets:bank", "assets:bank:checking", "assets:cash",
                                     "equity", "equity:opening", "expenses", "expenses:food",
                                     "expenses:food:groceries", "expenses:rent", "income", "income:salary"])
        self.assertEqual(got["assets"], M(("2119.75", "$")))
        self.assertEqual(got["assets:cash"], M(("19.75", "$")))
        self.assertEqual(rep.total, MixedAmount())

    def test_segment_sort(self):
        jr = j("""
            2025-01-01 X
                assets:bank2    $1
                assets:bank:a   $1
                assets:bank     $1
                assets:Cash     $1
                assets:bank-x   $1
                equity
            """)
        names = [r.account for r in balance(jr, flat=True).rows]
        self.assertEqual(names, ["assets:Cash", "assets:bank", "assets:bank:a", "assets:bank-x", "assets:bank2", "equity"])

    def test_zero_rows_and_parents(self):
        jr = j("""
            2025-01-01 X
                assets:a:x   $10
                assets:b    -$10
            2025-01-02 Y
                assets:c   $5
                assets:c  -$5
            """)
        rep = balance(jr)
        self.assertEqual([r.account for r in rep.rows], ["assets", "assets:a", "assets:a:x", "assets:b"])
        self.assertEqual(rows(rep)["assets"], MixedAmount())
        self.assertIn("assets:c", rows(balance(jr, empty=True)))

    def test_depth(self):
        rep = balance(j(BASIC), depth=1)
        self.assertEqual(list(rows(rep)), ["assets", "equity", "expenses", "income"])
        flat = rows(balance(j(BASIC), depth=2, flat=True))
        self.assertEqual(flat["expenses:food"], M(("30.25", "$")))
        self.assertNotIn("expenses", flat)

    def test_flat_is_own_balance(self):
        jr = j("""
            2025-01-01 X
                assets       $5
                assets:bank  $10
                equity
            """)
        self.assertEqual(rows(balance(jr, flat=True))["assets"], M(("5", "$")))
        self.assertEqual(rows(balance(jr))["assets"], M(("15", "$")))

    def test_dates_and_query(self):
        jr = j(BASIC)
        rep = balance(jr, Query(["assets"]), begin=date(2025, 1, 3), end=date(2025, 2, 1))
        self.assertEqual(rep.total, M(("1969.75", "$")))
        self.assertEqual(balance(jr, end=date(2025, 1, 1)).rows, [])

    def test_value_on_day_before_end(self):
        jr = j("""
            P 2025-01-15 EUR $1.05
            P 2025-01-31 EUR $1.10
            P 2025-02-01 EUR $2.00
            2025-01-10 X
                assets:eur   100 EUR
                equity
            2025-01-20 Y
                assets:usd   $5
                equity
            """)
        rep = balance(jr, Query(["assets"]), end=date(2025, 2, 1), value="$")
        self.assertEqual(rows(rep)["assets:eur"], M(("110.00", "$")))
        self.assertEqual(rep.total, M(("115.00", "$")))
        rep = balance(jr, Query(["assets:eur"]), value="$")  # on the last transaction's date
        self.assertEqual(rep.total, M(("105.00", "$")))


class Register(unittest.TestCase):
    def test_running_total(self):
        jr = j("""
            2025-01-01 Open
                assets:bank   $100
                equity
            2025-01-02 Fx | swap
                assets:bank   -$10
                assets:bank   9 EUR
                equity
            """)
        rows = register(jr, Query(["bank"]))
        self.assertEqual([(r.date, r.payee, r.account) for r in rows],
                         [(date(2025, 1, 1), "Open", "assets:bank"), (date(2025, 1, 2), "Fx", "assets:bank"),
                          (date(2025, 1, 2), "Fx", "assets:bank")])
        self.assertEqual(rows[2].amount, M(("9", "EUR")))
        self.assertEqual(rows[2].total, M(("90", "$"), ("9", "EUR")))


class IncomeStatementTests(unittest.TestCase):
    def test_sections_and_net(self):
        jr = parse(textwrap.dedent(BASIC).lstrip("\n") + textwrap.dedent("""
            2025-02-03 Side gig
                Revenue:consulting   -$300
                assets:cash
            2025-02-04 Refund
                Expense:misc   -$5
                assets:cash
            """))
        st = income_statement(jr)
        self.assertEqual(rows(st.revenues), {"Revenue": M(("300", "$")), "Revenue:consulting": M(("300", "$")),
                                                  "income": M(("2000", "$")), "income:salary": M(("2000", "$"))})
        self.assertEqual(st.revenues.total, M(("2300", "$")))
        self.assertEqual(st.expenses.total, M(("925.25", "$")))
        self.assertEqual(st.net, M(("1374.75", "$")))
        st = income_statement(jr, begin=date(2025, 2, 1))
        self.assertEqual(st.net, M(("-595", "$")))


class PeriodReportTests(unittest.TestCase):
    def test_monthly_columns(self):
        rep = balance_by_period(j(BASIC), "monthly", Query(["expenses", "income"]))
        self.assertEqual(rep.periods, [(date(2025, 1, 1), date(2025, 2, 1)), (date(2025, 2, 1), date(2025, 3, 1))])
        self.assertEqual(rep.rows, [("expenses:food:groceries", [M(("30.25", "$")), MixedAmount()]),
                                    ("expenses:rent", [MixedAmount(), M(("900", "$"))]),
                                    ("income:salary", [M(("-2000", "$")), MixedAmount()])])
        self.assertEqual(rep.totals, [M(("-1969.75", "$")), M(("900", "$"))])

    def test_quarterly_depth_and_bounds(self):
        rep = balance_by_period(j(BASIC), "quarterly", depth=1, begin=date(2025, 1, 2), end=date(2025, 1, 20))
        self.assertEqual(rep.periods, [(date(2025, 1, 1), date(2025, 4, 1))])
        self.assertEqual([a for a, _ in rep.rows], ["assets", "equity", "expenses", "income"])

    def test_all_zero_rows_left_out(self):
        jr = j("""
            2025-01-01 X
                assets:a   $1
                assets:a  -$1
                assets:b   $2
                equity
            """)
        rep = balance_by_period(jr, "monthly")
        self.assertEqual([a for a, _ in rep.rows], ["assets:b", "equity"])


class Budgets(unittest.TestCase):
    JR = """
        ~ monthly  Budget
            expenses:food    $400
            expenses:rent  $1,000
            assets:bank
        ~ weekly from 2025-01-06
            expenses:coffee   $10
            assets:bank
        2025-01-03 Grocer
            expenses:food:groceries   $150.40
            assets:bank
        2025-01-20 Market
            expenses:food   $250.10
            assets:bank
        2025-01-31 Rent
            expenses:rent   $1,000
            assets:bank
        2025-02-03 Coffee
            expenses:coffee   3 EUR
            assets:bank
        """

    def test_rows_and_percent(self):
        rows = budget_report(j(self.JR), date(2025, 1, 1), date(2025, 3, 1))
        got = [(r.month, r.account, r.budget, r.actual, r.percent, r.over) for r in rows]
        self.assertEqual([(m, a) for m, a, *_ in got], [
            (date(2025, 1, 1), "expenses:coffee"), (date(2025, 1, 1), "expenses:food"),
            (date(2025, 1, 1), "expenses:rent"), (date(2025, 2, 1), "expenses:coffee"),
            (date(2025, 2, 1), "expenses:food"), (date(2025, 2, 1), "expenses:rent")])
        jan_coffee, jan_food, jan_rent, feb_coffee, feb_food, feb_rent = got
        self.assertEqual(jan_coffee[2], M(("40", "$")))  # 6, 13, 20, 27 January
        self.assertEqual(jan_coffee[4], 0)
        self.assertEqual(jan_food[3], M(("400.50", "$")))
        self.assertEqual((jan_food[4], jan_food[5]), (100, False))
        self.assertEqual((jan_rent[4], jan_rent[5]), (100, False))
        self.assertEqual(feb_coffee[2], M(("40", "$")))  # 3, 10, 17, 24 February
        self.assertEqual((feb_coffee[4], feb_coffee[5]), (None, False))
        self.assertEqual(feb_rent[3], MixedAmount())
        self.assertEqual(feb_rent[4], 0)

    def test_over_and_rounding(self):
        jr = j("""
            ~ monthly
                expenses:fun   $30
                assets:bank
            2025-01-03 A
                expenses:fun   $30.15
                assets:bank
            2025-02-03 B
                expenses:fun   $0.15
                assets:bank
            """)
        jan, feb = budget_report(jr, date(2025, 1, 1), date(2025, 3, 1))
        self.assertEqual((jan.percent, jan.over), (101, True))
        self.assertEqual((feb.percent, feb.over), (1, False))

    def test_occurrences_start_and_bounds(self):
        jr = j("""
            ~ monthly from 2025-01-31
                expenses:x   $10
                assets:bank
            ~ quarterly
                expenses:y   $90
                assets:bank
            """)
        rows = budget_report(jr, date(2025, 2, 1), date(2025, 5, 1))
        x = [(r.month, r.budget) for r in rows if r.account == "expenses:x"]
        self.assertEqual(x, [(date(2025, 2, 1), M(("10", "$"))), (date(2025, 3, 1), M(("10", "$"))),
                             (date(2025, 4, 1), M(("10", "$")))])
        y = [(r.month, r.budget) for r in rows if r.account == "expenses:y"]
        self.assertEqual(y, [(date(2025, 2, 1), MixedAmount()), (date(2025, 3, 1), MixedAmount()),
                             (date(2025, 4, 1), M(("90", "$")))])
        self.assertEqual(rows[1].account, "expenses:y")
        self.assertIsNone(rows[1].percent)  # a zero budget


class Cli(unittest.TestCase):
    def run_cli(self, text, *argv, files=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "main.journal"
            path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
            for name, body in (files or {}).items():
                (Path(tmp) / name).write_text(body, encoding="utf-8")
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = main(["-f", str(path), *argv])
                except SystemExit as e:
                    code = e.code
            return code, out.getvalue(), err.getvalue().replace(str(path), "main.journal")

    SMALL = textwrap.dedent("""
        2025-01-01 Open
            assets:bank   $480.00
            assets:cash    $20.00
            equity
        2025-01-02 Dinner
            expenses:food   $40.00
            assets:bank
        """).lstrip("\n")

    def test_balance_tree(self):
        code, out, _ = self.run_cli(self.SMALL, "balance")
        self.assertEqual(code, 0)
        self.assertEqual(out, "\n".join([
            "             $460.00  assets",
            "             $440.00    bank",
            "              $20.00    cash",
            "            -$500.00  equity",
            "              $40.00  expenses",
            "              $40.00    food",
            "--------------------",
            "                   0",
        ]) + "\n")

    def test_balance_flat_multi_commodity(self):
        code, out, _ = self.run_cli("""
            2025-01-01 Open
                assets:bank   $5
                assets:bank   7 EUR
                equity
            """, "--flat", "bal", "assets")
        self.assertEqual(out, "\n".join([
            "                  $5  assets:bank",
            "               7 EUR",
            "--------------------",
            "                  $5",
            "               7 EUR",
        ]) + "\n")

    def test_register(self):
        code, out, _ = self.run_cli(self.SMALL, "reg", "bank")
        def row(d, payee, acct, amt, tot):
            return f"{d} {payee:<20} {acct:<24} {amt:>14} {tot:>14}"
        self.assertEqual(out, row("2025-01-01", "Open", "assets:bank", "$480.00", "$480.00") + "\n"
                         + row("2025-01-02", "Dinner", "assets:bank", "-$40.00", "$440.00") + "\n")

    def test_register_extra_lines(self):
        code, out, _ = self.run_cli("""
            2025-01-01 A very long payee name here
                assets:a-very-long-account-name:sub   $1.00
                equity
            2025-01-02 B
                assets:a-very-long-account-name:sub   2.00 EUR
                equity
            """, "register", "assets")
        lines = out.splitlines()
        self.assertEqual(lines[0], "2025-01-01 A very long payee na assets:a-very-long-accou "
                                   + "$1.00".rjust(14) + " " + "$1.00".rjust(14))
        self.assertEqual(lines[1], "2025-01-02 " + "B".ljust(20) + " assets:a-very-long-accou "
                                   + "2.00 EUR".rjust(14) + " " + "$1.00".rjust(14))
        self.assertEqual(lines[2], " " * 56 + " " + " " * 14 + " " + "2.00 EUR".rjust(14))
        self.assertEqual(len(lines), 3)

    def test_income_statement(self):
        code, out, _ = self.run_cli(self.SMALL + "2025-01-03 Pay\n    income:pay  -$100\n    assets:bank\n", "is")
        self.assertEqual(out, "\n".join([
            "Revenues:",
            "             $100.00  income",
            "             $100.00    pay",
            "--------------------",
            "             $100.00",
            "",
            "Expenses:",
            "              $40.00  expenses",
            "              $40.00    food",
            "--------------------",
            "              $40.00",
            "",
            "Net:",
            "              $60.00",
        ]) + "\n")

    def test_budget(self):
        code, out, _ = self.run_cli("~ monthly\n    expenses:food  $30\n    assets:bank\n" + self.SMALL,
                                    "-b", "2025-01-01", "-e", "2025-02-01", "budget")
        self.assertEqual(out, "2025-01 " + "expenses:food".ljust(24) + " " + "$40.00".rjust(14) + " "
                         + "$30.00".rjust(14) + "   133% over\n")

    def test_value_and_depth(self):
        code, out, _ = self.run_cli("""
            P 2025-01-01 EUR $1.50
            2025-01-01 Open
                assets:eur:wallet   10 EUR
                equity
            """, "-V", "$", "--depth", "2", "bal", "assets")
        self.assertEqual(out, "              $15.00  assets\n              $15.00    eur\n"
                              "--------------------\n              $15.00\n")

    def test_errors(self):
        code, out, err = self.run_cli("2025-01-01 X\n    a  $1\n    b  $1\n", "bal")
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "ledger: main.journal:1: transaction does not balance")
        code, _, _ = self.run_cli(self.SMALL, "frobnicate")
        self.assertEqual(code, 2)

    def test_module_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.journal"
            path.write_text(textwrap.dedent(self.SMALL).lstrip("\n"), encoding="utf-8")
            proc = subprocess.run([sys.executable, "-m", "ledger", "-f", str(path), "--depth", "1", "bal"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  cwd=os.environ.get("SWARMBENCH_WORKSPACE") or os.getcwd())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.splitlines()[0], "             $460.00  assets")


class Files(unittest.TestCase):
    def test_load_from_disk_with_include(self):
        with tempfile.TemporaryDirectory() as tmp:
            sub = Path(tmp) / "y2025"
            sub.mkdir()
            (Path(tmp) / "main.journal").write_text("include y2025/jan.journal\n", encoding="utf-8")
            (sub / "jan.journal").write_text("2025-01-01 X\n    a   $1\n    b\n", encoding="utf-8")
            jr = load(str(Path(tmp) / "main.journal"))
        self.assertEqual(len(jr.transactions), 1)
        self.assertEqual(Path(jr.transactions[0].path).name, "jan.journal")


if __name__ == "__main__":
    unittest.main()
