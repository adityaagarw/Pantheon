# Finance close

Tools for Finance's month-end close. `CLOSE_POLICY.md` is the policy: what the close does,
step by step, and the result it produces.

```
python -m close data/2026-03 2026-03-31     # writes out/close-2026-03.json
python -m unittest discover -s tests        # the tests
```

- `close/money.py`: rounding and formatting money (done).
- `close/banks.py`, `close/fx.py`, `close/report.py`: started, not written.
- `close/__main__.py`: the command (done).
- `data/<YYYY-MM>/`: a month's statements, ledger, rates, counterparties and opening
  balances, as Finance exports them. Don't edit them.

Python 3.10+, standard library only.
