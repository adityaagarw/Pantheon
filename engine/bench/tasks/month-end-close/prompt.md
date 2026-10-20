Finance needs March closed. Build the `close` package so that it follows
`CLOSE_POLICY.md` exactly, then close March.

The policy is long and every detail counts: the auditors re-run the close on other
months and compare the result line by line. Read all of it before you start.

Do this:

1. Write the bank statement loaders, the exchange rates, the ledger and aging, the
   matching and the report, as the policy describes (`close/banks.py`, `close/fx.py`,
   `close/report.py`; add modules as you need). The parts are fairly independent, so
   they can be built at the same time once the shapes they share are agreed.
2. Run `python -m close data/2026-03 2026-03-31`, so that `out/close-2026-03.json` is
   in the work folder.
3. Add tests in `tests/` (unittest) for the tricky rules: number formats, statement
   order, fees, partial payments, overpayments, ambiguous matches, aging buckets,
   rounding and FX.
4. Don't change `CLOSE_POLICY.md` or anything in `data/`.
