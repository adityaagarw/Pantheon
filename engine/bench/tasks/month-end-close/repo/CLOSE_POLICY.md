# Month-end close policy (Finance, v4)

This is how Finance closes a month. The `close` package must follow it exactly: the
auditors re-run it on other months.

Money is `decimal.Decimal`, never float. Round only where this policy says, and always
**half up** to cents (`close.money.cents`).

## 1. Bank statements

Every month has two statements in `data/<YYYY-MM>/`.

### Northwind Bank (USD): `bank_northwind.csv`

- Comma separated, header `Date,Description,Amount,Balance`.
- Dates are `MM/DD/YYYY`.
- Amounts and balances look like `1,234.50`, `-1,234.50` or `(1,234.50)`. Parentheses
  mean negative.
- The newest line comes first.
- Descriptions may have spaces around them. Strip them.
- The file ends with a footer row (for example `End of statement,,,`). Skip every row
  whose date is not a valid date.

### Eurobank (EUR): `bank_eurobank.csv`

- UTF-8 with a byte order mark, semicolon separated, fields may be quoted (a quoted
  description may contain `;`).
- Header `Buchungstag;Valuta;Verwendungszweck;Betrag;Waehrung`. Use `Buchungstag`
  (booking date) as the date, and `Verwendungszweck` as the description.
- Dates are `DD.MM.YYYY`. Amounts look like `1.234,50` or `-1.234,50`.
- The oldest line comes first. There is no footer.

### Bank lines

`close.banks.load_northwind(path)` and `close.banks.load_eurobank(path)` return a list
of bank lines in **file order**. A bank line is a dict:

| key | value |
| --- | --- |
| `id` | `"<bank>:<n>"`: `bank` is `northwind` or `eurobank`, `n` is the line's position among the kept rows of its file, from 1, in file order |
| `bank` | `"northwind"` or `"eurobank"` |
| `date` | `datetime.date` |
| `description` | `str`, stripped |
| `amount` | `Decimal`, negative for money going out |
| `currency` | `"USD"` (Northwind) or `"EUR"` (Eurobank) |
| `balance` | `Decimal` (Northwind only; Eurobank lines have no `balance` key) |

## 2. The ledger

`ledger.csv` lists open invoices (customers owe us) and bills (we owe suppliers):
`id,kind,counterparty,currency,amount,paid,issued,due`. `kind` is `invoice` or `bill`,
`paid` is what was paid in earlier months, dates are ISO. An item's **open amount** is
`amount - paid`. Items whose open amount is 0 are closed and never match.

`counterparties.json` maps each counterparty to the names it appears under on bank
statements: `{"Northwind Trading": ["NW TRADING LLC", "NORTHWIND TRD"], ...}`.

## 3. Matching bank lines to the ledger

Go through the bank lines of both banks in order of **date, then bank (`eurobank`
before `northwind`), then line number**. Open amounts change as lines are matched, and
later lines see the new open amounts.

A line with a positive amount can only match invoices, a negative one only bills, and only
items in the line's currency. Below, `P` is the line's amount without its sign.

1. **Bank charges.** If the description contains `ACCOUNT FEE` or starts with `FEE`
   (any case), it's a bank charge. It matches nothing.
2. **References.** Find the item ids in the description: `INV-<digits>` or
   `BILL-<digits>`, any case, matching the ledger's ids. Keep those that exist, are
   still open, and have the right kind and currency. If there is at least one, let `S`
   be the sum of their open amounts:
   - `P == S`: every referenced item is paid in full. The match kind is `exact` for one
     item, `multi` for more.
   - `P < S`, `S - P <= 25.00` and the line is incoming: the customer's bank kept a fee.
     Every referenced item is paid in full, and the fee is `S - P`. Kind `fee`.
   - `P < S` otherwise: a partial payment. Apply `P` to the referenced items in order of
     due date, then id. Each takes as much as it can (up to its open amount) until `P`
     runs out. Kind `partial`.
   - `P > S`: every referenced item is paid in full, and `P - S` is an overpayment by the
     counterparty of the first referenced item (in the order they appear in the
     description). Kind `overpayment`.
3. **Counterparty.** If no references were kept, look for counterparties whose aliases
   appear in the description (any case). The candidates are their open items of the
   right kind and currency whose open amount is exactly `P`. One candidate: it's paid in
   full, kind `alias`. None: the line is unmatched with reason `no match`. More than one:
   unmatched, reason `ambiguous`.

What each item received in a match (its **applied** amount) is how much its open amount
went down.

## 4. Exchange rates

`fx.csv` has `date,eur_usd` (dollars per euro) for working days. The rate for a day is
the rate on that day or, if there is none, on the closest earlier day in the file. There is
no rate before the first day: that's an error (`ValueError`).

`close.fx.Rates.load(path)` reads the file. `rates.rate(day)` gives the rate as a
`Decimal`, and `rates.to_usd(amount, currency, day)` converts: USD stays as it is, EUR is
`cents(amount * rate(day))`.

## 5. The close

`close.report.run(data_dir, close_date)` closes the month and returns the result below.
Money is a string with exactly two decimals (`"1234.50"`, `"-3.10"`). The command
`python -m close <data_dir> <YYYY-MM-DD>` writes it, as JSON with an indent of 2, to
`out/close-<YYYY-MM>.json`.

| key | value |
| --- | --- |
| `period_end` | the close date, ISO |
| `bank_lines` | how many bank lines there were (both banks) |
| `matches` | one entry per matched line, in matching order: `{"line": id, "kind": kind, "items": {item id: applied amount}}`. Items are in the order they were applied for `partial`, and in the order they appear in the description otherwise (an id written twice counts once) |
| `bank_charges` | ids of the bank charge lines, in matching order |
| `unmatched` | `{"line": id, "reason": "no match" or "ambiguous"}`, in matching order |
| `overpayments` | `{"line": id, "counterparty": name, "currency": code, "amount": money}`, in matching order |
| `open_items` | every item still open after matching, sorted by id: `{"id", "kind", "counterparty", "currency", "open": money, "bucket": bucket}` |
| `ar_aging_usd` | open invoices by bucket, in USD at the close date's rate: a sum per bucket of each item's converted open amount |
| `ap_aging_usd` | the same for open bills |
| `fees_usd` | fees kept by customers' banks (`fee` matches) plus bank charges (their amount without sign), each converted at its line's date and then added up |
| `fx_gain_loss_usd` | for every amount applied to a EUR item: `cents(applied * (rate(line date) - rate(item issued)))`, added up; for bills the sign is turned around (a dearer euro costs us). USD items have no FX result |
| `cash` | per bank: `{"currency": code, "opening": money, "closing": money}`. The opening balances are in `opening_balances.json`. Closing is opening plus every line's amount |
| `cash_usd_total` | both closing balances in USD at the close date's rate, added up |

Buckets come from how many days the item is past due on the close date: `current` (not
past due), `1-30`, `31-60`, `61-90` and `90+`. Every aging has all five buckets, `"0.00"`
when empty.

Northwind's statement shows a running balance: check it. The closing balance you compute
must equal the balance on the statement's newest line, or `run` raises `ValueError`.
