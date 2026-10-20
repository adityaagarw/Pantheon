# Support SLA reporting

`SLA_POLICY.md` is Support's SLA policy and the report it defines. `data/` is the export
for the weeks up to the end of March (tickets, customers, and when it was taken).

```
python -m sla data             # writes out/report.json
python -m unittest discover -s tests
```

`sla/report.py` is a first attempt that ignores most of the policy.

Python 3.10+, standard library only.
