"""The SLA report (SLA_POLICY.md, section 7)."""

import json
from pathlib import Path


def build(data_dir):
    """The report for the export in data_dir, as a JSON-ready dict.

    A first attempt from last quarter: it counts tickets per week and nothing else. It
    ignores regions, business time, duplicates and every SLA rule.
    """
    data = Path(data_dir)
    weeks = {}
    for line in (data / "tickets.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            ticket = json.loads(line)
            week = ticket["created"][:10]
            weeks[week] = weeks.get(week, 0) + 1
    return {"as_of": (data / "as_of.txt").read_text(encoding="utf-8").strip(),
            "weeks": [{"week": w, "tickets": n} for w, n in sorted(weeks.items())],
            "breaches": []}
