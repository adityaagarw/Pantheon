"""Instances in a window, and the agenda (RECUR.md section 5)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from calrule.expand import occurrences
from calrule.localtime import from_utc, localize, utc_of


DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True)
class Instance:
    uid: str
    summary: str
    start: datetime
    end: datetime


def _overlaps(s, e, start, end):
    if s == e:
        return start <= s < end
    return s < end and e > start


def instances(events, start, end):
    out = []
    for ev in events:
        moved = {utc_of(k): v for k, v in ev.overrides.items()}
        after = start - ev.duration - timedelta(microseconds=1)
        for s in occurrences(ev.rule, ev.start, ev.zone, exdates=ev.exdates,
                             rdates=ev.rdates, after=after):
            if s >= end:
                break
            if utc_of(s) in moved:
                continue
            e = from_utc(utc_of(s + ev.duration), ev.zone)
            if _overlaps(s, e, start, end):
                out.append(Instance(ev.uid, ev.summary, s, e))
        for ov in moved.values():
            if ov is None:
                continue
            s = localize(ov.start, ov.zone)
            e = from_utc(utc_of(s + ov.duration), ov.zone)
            if _overlaps(s, e, start, end):
                out.append(Instance(ev.uid, ov.summary, s, e))
    return sorted(out, key=lambda i: (utc_of(i.start), i.uid))


def agenda(events, start, end, zone):
    found = instances(events, start, end)
    if not found:
        return "No events.\n"
    blocks, current, lines = [], None, []
    for inst in found:
        s = from_utc(utc_of(inst.start), zone)
        e = from_utc(utc_of(inst.end), zone)
        if s.date() != current:
            if lines:
                blocks.append("\n".join(lines) + "\n")
            current, lines = s.date(), [f"{DAYS[s.weekday()]} {s.date().isoformat()}"]
        when = s.strftime("%H:%M")
        if inst.end != inst.start:
            when += "-" + e.strftime("%H:%M")
            days = (e.date() - s.date()).days
            if days:
                when += f"+{days}"
        title = " ".join(inst.summary.splitlines()) or "(no title)"
        lines.append(f"  {when}  {title}")
    blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks)
