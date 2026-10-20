"""Business time (SLA_POLICY.md, section 2)."""

from datetime import date, datetime, time, timedelta, timezone
from fractions import Fraction

REGIONS = {
    "Americas": (timedelta(hours=-5), time(8, 0), time(17, 0)),
    "EMEA": (timedelta(hours=1), time(9, 0), time(17, 30)),
    "APAC": (timedelta(hours=8), time(9, 0), time(18, 0)),
}
HOLIDAYS = {
    "Americas": {"2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25", "2026-07-03", "2026-09-07"},
    "EMEA": {"2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-14", "2026-05-25"},
    "APAC": {"2026-01-01", "2026-02-17", "2026-02-18", "2026-04-03", "2026-05-01", "2026-05-31"},
}


def local(moment, region):
    return moment.astimezone(timezone(REGIONS[region][0]))


def seconds(start, end, region):
    """Whole business seconds between two aware datetimes."""
    if end <= start:
        return 0
    offset, opens, closes = REGIONS[region]
    tz = timezone(offset)
    a, b = start.astimezone(tz), end.astimezone(tz)
    total, day = 0, a.date()
    while day <= b.date():
        if day.weekday() < 5 and day.isoformat() not in HOLIDAYS[region]:
            lo = max(a, datetime.combine(day, opens, tz))
            hi = min(b, datetime.combine(day, closes, tz))
            if hi > lo:
                total += int((hi - lo).total_seconds())
        day += timedelta(days=1)
    return total


def calendar_seconds(start, end):
    return max(0, int((end - start).total_seconds()))


def minutes(start, end, region):
    return Fraction(seconds(start, end, region), 60)


__all__ = ["minutes", "seconds", "calendar_seconds", "local", "REGIONS", "HOLIDAYS", "date"]
