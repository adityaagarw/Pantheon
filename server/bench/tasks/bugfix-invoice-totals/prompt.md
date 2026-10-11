The finance team reported wrong invoice totals. Read `ISSUE.md` for the ticket.
The billing rules are in `README.md`. The rules in the README are correct. The code is wrong.

Do this:

1. Fix the code in `invoicing/calc.py` so that it follows every billing rule in the README.
2. Fix every problem in the ticket.
3. Add a regression test for each problem in `tests/`.
4. Keep the public functions `invoice_summary(items, discount=None)` and
   `invoice_total(items, discount=None)`. Other code calls them.
