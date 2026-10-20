# ledger

A plain-text double-entry accounting engine: journal files in, checked books and reports
out. The specification is `LEDGER.md`; `ledger/dates.py` and `ledger/errors.py` are done,
everything else is to build.

```
python -m ledger -f data/household.journal balance
python -m unittest discover -s tests        # the tests
```

Standard library only (Python 3.10+).
