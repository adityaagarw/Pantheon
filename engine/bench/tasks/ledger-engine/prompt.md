Build the accounting engine specified in `LEDGER.md`: the `ledger` package with amounts and
mixed amounts, the journal parser, market prices, queries, the balance, register,
income-statement and period reports, budgets, and the `python -m ledger` command line.
`ledger/dates.py` and `ledger/errors.py` are done.

The books are checked against the spec exactly: every parsing rule and error, display
precision and rounding, balance assertions, which price wins, which rows a report shows
and in what order, and the command line's output character for character. The public API
is section 8.

The parts are large and fairly separate (amounts; the parser; prices and queries; reports;
budgets; the command line), but they meet at the shapes in section 8, so agree those
first. Add tests in `tests/` (unittest) for each part. Don't change `LEDGER.md`,
`ledger/dates.py` or `ledger/errors.py`.
