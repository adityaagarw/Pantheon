# calrule

Recurring events: calendar files in, an agenda out. The specification is `RECUR.md`;
`calrule/zones.py` and `calrule/errors.py` are done, everything else is to build.

```
python -m calrule data/team.ics --from 2025-03-03 --to 2025-03-17 --tz Europe/Berlin
python -m unittest discover -s tests        # the tests
```

Standard library only (Python 3.10+).
