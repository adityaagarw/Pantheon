"""Local time in a zone (RECUR.md section 1)."""

from datetime import timedelta, timezone

from calrule import zones


def to_utc(local, zone):
    offsets = {zones.offset_at(zone, local - timedelta(hours=36)),
               zones.offset_at(zone, local + timedelta(hours=36))}
    fits = [o for o in offsets if zones.offset_at(zone, local - o) == o]
    if fits:
        return local - max(fits)  # in an overlap, the first instant is the larger offset's
    # A gap: the offset in force before it.
    return local - zones.offset_at(zone, local - timedelta(hours=36))


def from_utc(utc, zone):
    offset = zones.offset_at(zone, utc)
    return (utc + offset).replace(tzinfo=timezone(offset))


def localize(local, zone):
    return from_utc(to_utc(local, zone), zone)


def utc_of(aware):
    """An aware datetime as a naive UTC one."""
    return aware.astimezone(timezone.utc).replace(tzinfo=None)
