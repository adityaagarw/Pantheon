# Ledger engine: specification

A plain-text double-entry accounting engine in the spirit of ledger and hledger: a journal
file is parsed, checked, and turned into reports. Python 3.10+, standard library only.
Money is `decimal.Decimal`, never float.

The package is `ledger/`. `ledger/dates.py` and `ledger/errors.py` are given and must not
change. Everything else is yours to write, to this spec. Every name in section 8 is the
public API the checks use.

---

## 1. Amounts (`ledger/amounts.py`)

### 1.1 Writing an amount

An amount is a number with an optional commodity.

- The **number**: digits, optionally grouped in threes by commas (`1,234,567`), optionally
  with a decimal point and decimals (`1,234.50`, `0.125`). Groups that aren't three
  digits (`12,34`) are an error. A number may not start with `.`.
- The **commodity** is either
  - a **symbol**: one character that is not a letter, digit, space, `.`, `,`, `-`, `+`,
    `;`, `=`, `@`, `(`, `)`, `[`, `]` or `"` (for example `$`, `€`, `£`, `¥`), written
    directly before or after the number, with or without one space; or
  - a **code**: one or more ASCII letters (`USD`, `EUR`, `AAPL`, `hours`), written before
    or after the number, separated from it by one or more spaces.
  - Or none: a bare number has the empty commodity `""`.
- The **sign**: one `-`, either before everything (`-$5`, `-5 EUR`) or directly before the
  number (`$-5`, `EUR -5`). Two signs, or a `+`, are an error.
- Commodities are exactly as written: `$` and `USD` are different commodities, and so are
  `EUR` and `eur`.

### 1.2 Display precision

Each commodity has a display precision (the number of decimals it is shown with):

1. the decimals of the example amount in a `commodity` directive for it (section 2.6), else
2. the largest number of decimals it is **written** with anywhere in the journal (in
   posting amounts, costs, balance assertions and `P` prices; amounts that the engine
   works out never count), else
3. 2.

### 1.3 Showing an amount

- The quantity is rounded to the commodity's precision, half away from zero
  (`0.005` → `0.01`, `-0.005` → `-0.01`).
- The integer part is grouped in threes by commas.
- A **symbol** commodity goes directly before the number: `$1,234.50`, negative `-$1,234.50`.
- A **code** commodity goes after the number with one space: `1,234.50 EUR`, negative
  `-1,234.50 EUR`.
- No commodity: just the number: `1,234.50`.
- A quantity that rounds to zero has no sign: `$0.00`, never `-$0.00`.

### 1.4 Mixed amounts

A mixed amount is a sum of amounts in any number of commodities (one quantity per
commodity). Adding and subtracting work per commodity; a commodity whose quantity becomes
exactly zero is dropped. It is **zero** when every quantity rounds to zero at its display
precision. Shown, it is one line per commodity whose quantity doesn't round to zero, in
commodity order (Python string order); a zero mixed amount shows as the single line `0`.

---

## 2. The journal (`ledger/parser.py`)

A journal is UTF-8 text made of transactions, periodic transactions, directives and
comments. Line numbers are 1-based, in the file they are in.

### 2.1 Lines

- A line starting with `;`, `#`, `*` or `%` in the first column is a comment. It ends the
  current transaction.
- A blank line ends the current transaction.
- A line starting with a date in the first column starts a transaction; `~` starts a
  periodic transaction; a directive keyword (section 2.6) is a directive and ends the
  current transaction.
- A line starting with spaces or tabs belongs to the current transaction: a comment (its
  first non-blank character is `;`) or a posting. One with no current transaction is an
  error ("posting outside a transaction").
- Anything else is an error ("unexpected line").

### 2.2 Transactions

```
DATE [STATUS] [(CODE)] DESCRIPTION [; COMMENT]
```

- `DATE` is `YYYY-MM-DD` or `YYYY/MM/DD`.
- `STATUS` is `*` (cleared) or `!` (pending); none is `""`.
- `CODE` is anything in parentheses, for example `(INV-1042)`; none is `""`.
- `DESCRIPTION` is the rest up to `;`, stripped. When it contains `|`, the part before the
  first `|` (stripped) is the **payee** and the part after (stripped) the **note**;
  otherwise the payee is the whole description and the note is `""`.
- The comment after `;`, and the comments on indented comment lines before the first
  posting, are the transaction's comment (joined with newlines, each stripped).

### 2.3 Postings

```
    [STATUS] ACCOUNT  [AMOUNT [@ UNIT_PRICE | @@ TOTAL_PRICE]] [= ASSERTION] [; COMMENT]
```

- At least one space or tab of indentation, then an optional status `*` or `!` followed by
  a space.
- The **account** name ends at two or more spaces, a tab, `;`, `=` or the end of the line.
  It is made of segments separated by `:` (`assets:bank:checking`). Single spaces inside
  are part of it (`expenses:eating out`).
- An account in parentheses, `(budget:food)`, is **virtual**: it doesn't take part in
  balancing and must have an amount. The parentheses are not part of the name.
  Square brackets are not supported (an error).
- A **cost**: `10 AAPL @ $150.00` (per unit) or `10 AAPL @@ $1,500.00` (total). The
  posting's cost is the total, with the sign of the quantity: `-10 AAPL @ $150` costs
  `-$1,500`, and so does `-10 AAPL @@ $1,500`. A posting with a cost must have an amount.
- An indented comment line after a posting adds to that posting's comment.

### 2.4 Balancing

- A posting's **weight** is its cost if it has one, else its amount.
- In every transaction the weights of its non-virtual postings must sum to zero, per
  commodity, after rounding to that commodity's display precision; otherwise the error is
  "transaction does not balance" at the transaction's line.
- One non-virtual posting may leave out its amount: it gets the negated sum of the other
  weights. If that sum has several commodities, the posting becomes one posting per
  commodity (in commodity order, same account and line); if the sum is zero it gets the
  amount `0` with no commodity. Two non-virtual postings without an amount are an error
  ("more than one posting without an amount").
- A transaction needs at least one posting ("transaction has no postings").

### 2.5 Balance assertions

`= AMOUNT` on a posting asserts the account's balance in that amount's commodity right
after this posting: the sum of every posting so far to exactly that account (not its
subaccounts, virtual postings included) in that commodity. Postings count in journal
order: by date, then by their order in the files (includes counted where they are
included). The comparison rounds to the commodity's precision. A failure is the error
`balance assertion failed for ACCOUNT: expected X, got Y` (both shown as in 1.3) at the
posting's line. A posting may assert without an amount (its amount is worked out first).

### 2.6 Directives

All start in the first column.

- `include PATH`: parses that file at this point. A relative path is relative to the
  directory of the file that includes it. Including a file that is already being included
  (a cycle) is the error "include cycle: PATH".
- `commodity AMOUNT`: sets that commodity's display precision from the decimals of the
  example (`commodity 1,000.000 BTC`, `commodity $1,000.00`). `commodity CODE` with no
  number is allowed and changes nothing.
- `P DATE COMMODITY AMOUNT`: a market price: one unit of `COMMODITY` was worth `AMOUNT` on
  `DATE` (`P 2025-01-31 EUR $1.08`).
- `alias FROM = TO`: from here on, an account named `FROM`, or starting with `FROM:`, has
  that part replaced by `TO` (`alias bank = assets:bank:checking` turns `bank:fees` into
  `assets:bank:checking:fees`). Every alias is tried in the order they were defined, each
  once, on the result of the ones before it (so a second alias can rewrite what the first
  produced), on later postings in this file and in files included later. `end aliases`
  forgets them all.
- `account NAME`: declares an account; allowed and otherwise ignored.
- Any other word in the first column that isn't a date is "unexpected line".

### 2.7 Tags

A comment line (of a transaction or a posting) may hold tags: `name:` or `name:value`,
where the name is a letter followed by letters, digits, `_` or `-`, and the value runs to
the next `,` or the end of that line, stripped. `; trip:berlin, reimbursable:` gives
`{"trip": "berlin", "reimbursable": ""}`. A posting has its transaction's tags plus its own
(its own win).

### 2.8 Periodic transactions

```
~ PERIOD  [DESCRIPTION]
    expenses:food   $400
    assets:bank
```

`PERIOD` is `weekly`, `monthly`, `quarterly` or `yearly`, optionally followed by
`from DATE`; two or more spaces separate it from the description. Anything else is the
error "bad period". Its postings follow the rules of 2.3 and 2.4. Periodic transactions are
used only by budgets (section 6); they are not in `Journal.transactions`.

### 2.9 Errors

Every problem in a journal raises `ledger.errors.JournalError(path, line, message)`, whose
text is `PATH:LINE: MESSAGE`. `path` is the file's path as given (or `-` when there is
none), `line` the line number in that file.

### 2.10 Order and prices

- `Journal.transactions` is in journal order: by date, then by order in the files
  (stable: a transaction included from another file sits where its `include` was).
- `Journal.prices` lists every price in file order: each `P` directive, and each posting
  with a cost, which records the unit price `|cost| / |quantity|` of the posting's
  commodity in the cost's commodity on the transaction's date.

---

## 3. Prices and value (`ledger/prices.py`)

- `PriceDB(journal)` knows the journal's prices.
- `rate(frm, to, on)`: what one unit of `frm` is worth in `to` on date `on`, as a
  `Decimal`. `1` when they are the same. Otherwise the most recent price on or before `on`
  among the direct prices (`frm` in `to`) and the inverse ones (`to` in `frm`, used as
  `1 / price`). When a direct and an inverse price share the most recent date, the direct
  one wins; among prices of the same direction and date, the last one in the file wins.
  No prices are chained through a third commodity. None found raises
  `ledger.errors.PriceError`.
- `value(mixed, to, on)`: the mixed amount with each commodity converted to `to` at
  `rate(...)`. A commodity with no rate stays as it is. No rounding happens here.

---

## 4. Queries (`ledger/query.py`)

`Query(terms)` takes a list of strings and decides which postings a report uses.

- A plain term is a regular expression searched in the posting's account,
  case-insensitively.
- `desc:REGEX` is searched in the transaction's description.
- `tag:NAME` matches a posting that has that tag (with any value); `tag:NAME=REGEX` one
  whose value for that tag the expression is searched in.
- `not:TERM` (any of the above after `not:`) excludes postings that `TERM` matches.
- A posting matches when it matches at least one plain term (or there are none), every
  `desc:` and `tag:` term, and no `not:` term. An empty query matches every posting.
- `Query.matches(posting)` returns `True` or `False`.

---

## 5. Reports (`ledger/reports.py`)

A report's `query` is a `Query` (section 4) or `None` (every posting). Every report takes
`begin` and `end` dates (or `None`): it uses the transactions with
`begin <= date < end`. Accounts sort by their segments (`assets:bank` before
`assets:bank2` before `assets:cash`; compare the lists of segments). Whether an amount is
zero uses the journal's display precisions (1.4).

### 5.1 `balance(journal, query=None, begin=None, end=None, depth=None, flat=False, empty=False, value=None)`

Returns a `BalanceReport` with `rows` (a list of `BalanceRow(account, amount)`) and `total`.

- The postings used are those in the date range that match the query (virtual included).
- `depth=N` (N ≥ 1): an account with more than N segments counts as its first N segments.
- **Tree** (the default): a row for each account that has postings and for each of its
  parent accounts, with the account's **inclusive** balance (its own postings and all its
  subaccounts'). A row is hidden when its balance is zero, unless `empty` is true, but a
  parent is always shown when any of its subaccounts is.
- **Flat**: a row only for each account that has postings (after `depth`), with its own
  balance (which, with `depth`, includes the deeper accounts it stands for). Zero rows are
  hidden unless `empty`.
- `total` is the sum of every posting used.
- `value="COMMODITY"`: every row's amount and the total are converted with
  `PriceDB.value(...)`, on the day before `end`, or, without `end`, on the date of the
  journal's last transaction.

### 5.2 `register(journal, query=None, begin=None, end=None)`

One `RegisterRow(date, payee, account, amount, total)` per matching posting, in journal
order: `amount` is the posting's amount as a mixed amount, `total` the running sum of the
amounts of the rows so far.

### 5.3 `income_statement(journal, begin=None, end=None, depth=None)`

Returns `IncomeStatement(revenues, expenses, net)`.

- `revenues`: a tree balance report (5.1) of the accounts whose first segment is `income`,
  `revenue` or `revenues` (any case), with every amount **negated** (so income shows as a
  positive number).
- `expenses`: a tree balance report of the accounts whose first segment is `expenses` or
  `expense` (any case).
- `net`: `revenues.total - expenses.total`.

### 5.4 `balance_by_period(journal, interval, query=None, begin=None, end=None, depth=None)`

Returns `PeriodReport(periods, rows, totals)`.

- `periods`: `ledger.dates.periods(interval, begin, end)`. Without `begin`, the first
  transaction's date; without `end`, the day after the last transaction's date.
- `rows`: `(account, [amount per period])` for each account (flat, after `depth`) that has
  a posting in any period, sorted, with the change in each period. Accounts whose every
  period is zero are left out.
- `totals`: the sum of each period's postings.
- Transactions outside the periods' span are not used.

---

## 6. Budgets (`ledger/budget.py`)

`budget_report(journal, begin, end)` returns a list of
`BudgetRow(month, account, budget, actual, percent, over)`, one for each month of
`dates.periods("monthly", begin, end)` (by `month`, its first day) and each **budget
account** (sorted, as in section 5), months first.

- The budget accounts are the accounts of the periodic transactions' postings that were
  written with an amount (the posting that balances it, without an amount, is not a budget
  account).
- A periodic transaction **occurs** on its `from` date, or without one on
  `dates.period_start(begin, PERIOD)`, and then every `PERIOD` after that
  (`dates.step(start, PERIOD, k)` for k = 1, 2, ...). Each occurrence on or after `begin`
  and before `end` adds its postings' amounts to `budget` for the month it falls in.
- `actual`: the sum of the postings in that month to the budget account or any of its
  subaccounts.
- `percent`: `actual / budget × 100` rounded half away from zero to a whole number, when
  `budget` and `actual` are each zero or a single amount, in the same commodity, and the
  budget isn't zero (an `actual` of zero counts as 0 in the budget's commodity); else `None`.
- `over`: `percent` is not `None` and is above 100.

---

## 7. The command line (`ledger/cli.py`, `python -m ledger`)

```
python -m ledger -f FILE [options] COMMAND [QUERY ...]
```

Options: `-b/--begin DATE`, `-e/--end DATE`, `--depth N`, `--flat`, `-E/--empty`,
`-V/--value COMMODITY`. Commands: `balance` (or `bal`), `register` (or `reg`),
`incomestatement` (or `is`), `budget`. `main(argv)` returns the exit code; `python -m ledger`
exits with it.

- Exit code 0 on success. A journal or price error: the message `ledger: TEXT` on stderr
  (TEXT is the error's text), exit code 1. A bad command or option: exit code 2.
- Amounts are shown as in 1.3. A mixed amount takes one line per commodity (1.4).

**balance**: for each row, its amount's lines, each right-aligned in 20 characters; the
first line is followed by two spaces and the account: in a tree, the last segment indented
by two spaces per level below the top (`assets` 0, `assets:bank` 2); flat, the full name.
Then a line of 20 `-`, then the total's lines right-aligned in 20 characters.

```
             $500.00  assets
             $480.00    bank
              $20.00    cash
             -$40.00  expenses
--------------------
             $460.00
```

**register**: each row is `DATE DESC ACCOUNT AMOUNT TOTAL`: the date `YYYY-MM-DD`, a space,
the payee cut or padded to 20, a space, the account cut or padded to 24, a space, the
amount right-aligned in 14, a space, the running total right-aligned in 14. When the
amount or total has more lines, the next lines show only those columns (the date, payee
and account columns as spaces). Trailing spaces are removed from every line.

**incomestatement**: `Revenues:`, the revenue rows as in balance, a line of 20 `-`, the
revenues total; a blank line; `Expenses:`, the expense rows, a line of 20 `-`, the expenses
total; a blank line; a line `Net:`, then the net's lines right-aligned in 20.

**budget**: for each row, `MONTH ACCOUNT ACTUAL BUDGET PERCENT`: the month `YYYY-MM`, a
space, the account padded to 24, a space, the actual right-aligned in 14, a space, the
budget right-aligned in 14, a space, the percent right-aligned in 5 followed by `%` (or
`-` right-aligned in 6 when it's `None`), and ` over` when over. Mixed amounts with more
than one line show only their first line here. Trailing spaces removed.

---

## 8. Public API

```python
from ledger.amounts import Amount, MixedAmount, parse_amount, format_amount
from ledger.parser import parse, load, Journal, Transaction, Posting, Periodic, Price
from ledger.prices import PriceDB
from ledger.query import Query
from ledger.reports import (balance, register, income_statement, balance_by_period,
                            BalanceReport, BalanceRow, RegisterRow, IncomeStatement, PeriodReport)
from ledger.budget import budget_report, BudgetRow
from ledger.errors import JournalError, PriceError
from ledger.cli import main
```

- `Amount(quantity, commodity)`: a frozen dataclass with `quantity` (`Decimal`) and
  `commodity` (`str`); `-amount` negates it.
- `parse_amount(text) -> Amount` (raises `ValueError` for anything section 1.1 doesn't
  allow); `format_amount(amount, precision) -> str` (section 1.3).
- `MixedAmount(amounts=())`: `+` and `-` with another `MixedAmount` or an `Amount`, unary
  `-`; `amounts()` the nonzero amounts in commodity order; `get(commodity)` the quantity
  (`Decimal("0")` if none); `==` with another `MixedAmount` compares the exact quantities;
  `is_zero(precisions)` and `lines(precisions)` (section 1.4), where `precisions` is a
  mapping from commodity to precision and any commodity missing from it uses 2.
- `parse(text, path="-", loader=None) -> Journal`; `loader(path) -> str` reads included
  files (by default, from disk as UTF-8). `load(path) -> Journal` reads and parses a file.
- `Journal`: `transactions`, `periodic` (list of `Periodic`), `prices` (list of `Price`),
  `precisions` (dict: commodity → precision, for every commodity in the journal and in a
  `commodity` directive), `precision(commodity) -> int`, `format(amount) -> str`
  (an `Amount`, at its commodity's precision).
- `Transaction`: `date`, `status`, `code`, `description`, `payee`, `note`, `comment`,
  `tags`, `postings`, `path`, `line`.
- `Posting`: `account`, `amount` (an `Amount`, worked out if it was left out), `cost` (the
  total cost `Amount`, or `None`), `virtual`, `status`, `comment`, `tags`, `assertion`
  (`Amount` or `None`), `transaction`, `line`.
- `Periodic`: `interval`, `start` (`date` or `None`), `description`, `postings`
  (as `Posting`s, with `transaction` set to the `Periodic`), `path`, `line`.
- `Price`: `date`, `commodity`, `price` (an `Amount`).
- Reports return dataclasses with the fields named in sections 5 and 6.
