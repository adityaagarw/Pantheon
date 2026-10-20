Support leadership's SLA dashboard is wrong, and the board meeting is Friday. Rebuild the
`sla` package so that `python -m sla data` produces exactly the report that
`SLA_POLICY.md` defines, then run it so that `out/report.json` is in the work folder.

The policy is precise and the export is messy: times in several offsets, three regional
teams with their own hours and holidays, round-the-clock enterprise urgent tickets,
pauses while waiting for customers, reopened tickets, missing or junk priorities, and
customers who write in twice. The same code will run on next month's export.

The work splits into business time, priority and duplicates, measuring tickets, and the
report. Build them in parallel once their interfaces are agreed, and add tests in
`tests/` (unittest) for every rule. Don't change the policy or anything in `data/`.
