"""Time zones. Given: don't change this file.

Five zones with today's rules, used for every year. Times here are naive datetimes in UTC.
"""

from datetime import date, datetime, timedelta

from calrule.errors import UnknownZone

H = timedelta(hours=1)


def _nth_sunday(year, month, n):
    """The nth Sunday of a month (n = -1: the last)."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - 6) % 7)


def _at(day, hours):
    return datetime(day.year, day.month, day.day) + timedelta(hours=hours)


def _new_york(year):
    # Clocks go forward at 02:00 local standard time (07:00 UTC) on the second Sunday of
    # March and back at 02:00 local daylight time (06:00 UTC) on the first Sunday of November.
    return [(_at(_nth_sunday(year, 3, 2), 7), -5 * H, -4 * H),
            (_at(_nth_sunday(year, 11, 1), 6), -4 * H, -5 * H)]


def _berlin(year):
    # The EU changes at 01:00 UTC on the last Sundays of March and October.
    return [(_at(_nth_sunday(year, 3, -1), 1), 1 * H, 2 * H),
            (_at(_nth_sunday(year, 10, -1), 1), 2 * H, 1 * H)]


def _sydney(year):
    # Back at 03:00 local daylight time on the first Sunday of April, forward at 02:00 local
    # standard time on the first Sunday of October: both 16:00 UTC the day before.
    return [(_at(_nth_sunday(year, 4, 1), -8), 11 * H, 10 * H),
            (_at(_nth_sunday(year, 10, 1), -8), 10 * H, 11 * H)]


ZONES = {
    "UTC": (timedelta(0), None),
    "Asia/Kolkata": (timedelta(hours=5, minutes=30), None),
    "America/New_York": (-5 * H, _new_york),
    "Europe/Berlin": (1 * H, _berlin),
    "Australia/Sydney": (11 * H, _sydney),  # in daylight time at the start of each year
}


def check(zone):
    """The zone's name, or UnknownZone."""
    if zone not in ZONES:
        raise UnknownZone(zone)
    return zone


def transitions(zone, year):
    """[(utc instant, offset before, offset after)] of the changes in a year, in order."""
    rules = ZONES[check(zone)][1]
    return rules(year) if rules else []


def offset_at(zone, utc):
    """The zone's UTC offset in force at a UTC instant (a naive datetime in UTC)."""
    offset, rules = ZONES[check(zone)]
    if rules is None:
        return offset
    for year in (utc.year - 1, utc.year, utc.year + 1):
        for when, _before, after in rules(year):
            if when <= utc:
                offset = after
    return offset
