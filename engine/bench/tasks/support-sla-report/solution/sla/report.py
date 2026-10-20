"""Measuring tickets and the report (SLA_POLICY.md, sections 6 and 7)."""

import json
import math
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from sla.business import calendar_seconds, local, seconds
from sla.rules import mark_duplicates, priority, targets


def parse(text):
    return datetime.fromisoformat(str(text).replace("Z", "+00:00"))


def half_up(value):
    """Round a non-negative Fraction half up to an int."""
    return math.floor(Fraction(value) + Fraction(1, 2))


def measure(ticket, region, calendar, as_of):
    def span(a, b):
        return calendar_seconds(a, b) if calendar else seconds(a, b, region)

    created = ticket["created"]
    events = ticket["events"]
    first = next((e["at"] for e in events if e["type"] == "first_response"), None)
    first_s = span(created, first or as_of)
    statuses = [e for e in events if e["type"] == "status"]
    solved = statuses and statuses[-1]["status"] == "solved"
    end = statuses[-1]["at"] if solved else as_of
    paused = 0
    for i, e in enumerate(statuses):
        if e["status"] != "pending_customer" or e["at"] >= end:
            continue
        stop = statuses[i + 1]["at"] if i + 1 < len(statuses) else as_of
        paused += span(e["at"], min(stop, end, as_of))
    return {"first": first_s, "answered": first is not None,
            "resolution": span(created, end) - paused, "resolved": bool(solved)}


def build(data_dir):
    data = Path(data_dir)
    as_of_text = (data / "as_of.txt").read_text(encoding="utf-8").strip()
    as_of = parse(as_of_text)
    customers = json.loads((data / "customers.json").read_text(encoding="utf-8"))
    tickets = []
    for line in (data / "tickets.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            t = json.loads(line)
            t["created"] = parse(t["created"])
            t["events"] = [{**e, "at": parse(e["at"])} for e in t.get("events", [])]
            tickets.append(t)
    dups = mark_duplicates(tickets)

    groups, breaches = {}, []
    for t in tickets:
        info = customers.get(t["customer"], {"plan": "starter", "region": "Americas"})
        plan, region = info["plan"], info["region"]
        iso = local(t["created"], region).isocalendar()
        g = groups.setdefault((f"{iso[0]}-W{iso[1]:02d}", region), {
            "tickets": 0, "duplicates": 0, "fr_breaches": 0, "res_breaches": 0,
            "firsts": [], "resolutions": []})
        if t["id"] in dups:
            g["duplicates"] += 1
            continue
        g["tickets"] += 1
        prio = priority(t, plan)
        first_target, res_target = targets(plan, prio)
        m = measure(t, region, (plan, prio) == ("enterprise", "urgent"), as_of)
        if m["answered"]:
            g["firsts"].append(m["first"])
        if m["resolved"]:
            g["resolutions"].append(m["resolution"])
        for kind, value, target in (("first_response", m["first"], first_target),
                                    ("resolution", m["resolution"], res_target)):
            if value > target:
                g["fr_breaches" if kind == "first_response" else "res_breaches"] += 1
                breaches.append({"ticket": t["id"], "kind": kind, "plan": plan, "priority": prio,
                                 "minutes_over": half_up(Fraction(value - target, 60))})

    weeks = []
    order = {"Americas": 0, "EMEA": 1, "APAC": 2}
    for (week, region), g in sorted(groups.items(), key=lambda kv: (kv[0][0], order[kv[0][1]])):
        firsts = sorted(g["firsts"])
        median = None
        if firsts:
            n = len(firsts)
            mid = (Fraction(firsts[(n - 1) // 2]) + Fraction(firsts[n // 2])) / 2
            median = half_up(mid / 60)
        res = sorted(g["resolutions"])
        p90 = half_up(Fraction(res[math.ceil(0.9 * len(res)) - 1], 60)) if res else None
        weeks.append({"region": region, "week": week, "tickets": g["tickets"],
                      "duplicates": g["duplicates"],
                      "first_response_breaches": g["fr_breaches"],
                      "resolution_breaches": g["res_breaches"],
                      "median_first_response_minutes": median, "p90_resolution_minutes": p90})
    breaches.sort(key=lambda b: (b["ticket"], b["kind"]))
    return {"as_of": as_of_text, "weeks": weeks, "breaches": breaches}
