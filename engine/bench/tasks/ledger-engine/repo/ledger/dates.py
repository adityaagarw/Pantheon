"""Dates and report periods. Given: don't change this file."""

import re
from datetime import date, timedelta

INTERVALS = ("weekly", "monthly", "quarterly", "yearly")


def parse_date(text):
    """A date written YYYY-MM-DD or YYYY/MM/DD (month and day may have one digit)."""
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(text).strip())
    if not m:
        raise ValueError(f"bad date: {text!r}")
    return date(int(m[1]), int(m[2]), int(m[3]))


def add_months(d, n):
    """d moved n months on, the day kept or clamped to the month's last day."""
    month = d.month - 1 + n
    year, month = d.year + month // 12, month % 12 + 1
    days = [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    return date(year, month, min(d.day, days))


def period_start(d, interval):
    """The start of the calendar period of this interval that contains d (weeks start on Monday)."""
    if interval == "weekly":
        return d - timedelta(days=d.weekday())
    if interval == "monthly":
        return d.replace(day=1)
    if interval == "quarterly":
        return date(d.year, (d.month - 1) // 3 * 3 + 1, 1)
    if interval == "yearly":
        return date(d.year, 1, 1)
    raise ValueError(f"unknown interval: {interval!r}")


def step(d, interval, n=1):
    """d moved on by n intervals."""
    if interval == "weekly":
        return d + timedelta(days=7 * n)
    if interval == "monthly":
        return add_months(d, n)
    if interval == "quarterly":
        return add_months(d, 3 * n)
    if interval == "yearly":
        return add_months(d, 12 * n)
    raise ValueError(f"unknown interval: {interval!r}")


def periods(interval, begin, end):
    """The calendar periods of this interval from the one containing begin, while they start
    before end: a list of (start, end) pairs, each end exclusive."""
    out = []
    start = period_start(begin, interval)
    while start < end:
        nxt = step(start, interval)
        out.append((start, nxt))
        start = nxt
    return out
