"""Calendar files (RECUR.md section 4)."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from calrule import zones
from calrule.errors import CalendarError, RuleError, UnknownZone
from calrule.localtime import localize
from calrule.rule import parse_rule


@dataclass
class Event:
    uid: str
    summary: str
    zone: str
    start: datetime
    duration: timedelta = timedelta(0)
    rule: object = None
    exdates: list = field(default_factory=list)
    rdates: list = field(default_factory=list)
    overrides: dict = field(default_factory=dict)


def _lines(text):
    """[(line number, unfolded line)]."""
    out = []
    for n, raw in enumerate(re.split(r"\r?\n", text), 1):
        if raw[:1] in (" ", "\t") and out:
            out[-1][1] += raw[1:]
        elif raw.strip():
            out.append([n, raw])
    return [(n, s) for n, s in out]


def _property(n, line):
    i, quoted = 0, False
    while i < len(line):
        c = line[i]
        if c == '"':
            quoted = not quoted
        elif c == ":" and not quoted:
            break
        i += 1
    else:
        raise CalendarError(n, f"not a property: {line!r}")
    head, value = line[:i], line[i + 1:]
    name, *params = head.split(";")
    p = {}
    for item in params:
        k, _, v = item.partition("=")
        p[k.strip().upper()] = v.strip().strip('"')
    return name.strip().upper(), p, value


def _unescape(text):
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), text)


def _zone(n, params):
    tzid = params.get("TZID")
    if tzid is None:
        return None
    try:
        return zones.check(tzid)
    except UnknownZone as e:
        raise CalendarError(n, str(e)) from None


def _time(n, name, params, value):
    """(naive local time, zone) of a time value."""
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value.strip()):
        raise CalendarError(n, f"{name}: dates without a time are not supported")
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", value.strip())
    if not m:
        raise CalendarError(n, f"{name}: not a time: {value!r}")
    try:
        t = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S")
    except ValueError:
        raise CalendarError(n, f"{name}: not a time: {value!r}") from None
    if m.group(2):
        return t, "UTC"
    zone = _zone(n, params)
    if zone is None:
        raise CalendarError(n, f"{name} needs a TZID or a time in UTC")
    return t, zone


def _instant(n, name, params, value):
    t, zone = _time(n, name, params, value)
    return localize(t, zone)


def _duration(n, value):
    m = re.fullmatch(r"P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip())
    if not m or value.strip() in ("P", "PT") or value.strip().endswith("T"):
        raise CalendarError(n, f"DURATION: not a duration: {value!r}")
    w, d, h, mi, s = (int(g or 0) for g in m.groups())
    return timedelta(weeks=w, days=d, hours=h, minutes=mi, seconds=s)


def _event(begin, props):
    """An event from its properties; (event, recurrence id or None, cancelled)."""
    first = {}
    for n, name, params, value in props:
        first.setdefault(name, (n, params, value))
    if "UID" not in first:
        raise CalendarError(begin, "the event has no UID")
    if "DTSTART" not in first:
        raise CalendarError(begin, "the event has no DTSTART")
    n, params, value = first["DTSTART"]
    start, zone = _time(n, "DTSTART", params, value)
    begins = localize(start, zone)
    duration, has_duration = timedelta(0), False
    if "DTEND" in first:
        n, params, value = first["DTEND"]
        ends = _instant(n, "DTEND", params, value)
        if ends < begins:
            raise CalendarError(n, "DTEND is before DTSTART")
        duration, has_duration = ends - begins, True
    elif "DURATION" in first:
        n, _, value = first["DURATION"]
        duration, has_duration = _duration(n, value), True
    rule = None
    if "RRULE" in first:
        n, _, value = first["RRULE"]
        try:
            rule = parse_rule(value)
        except RuleError as e:
            raise CalendarError(n, str(e)) from None
    exdates, rdates = [], []
    for n, name, params, value in props:
        if name in ("EXDATE", "RDATE"):
            for v in value.split(","):
                (exdates if name == "EXDATE" else rdates).append(_instant(n, name, params, v))
    ev = Event(uid=first["UID"][2].strip(),
               summary=_unescape(first["SUMMARY"][2]) if "SUMMARY" in first else "",
               zone=zone, start=start, duration=duration, rule=rule,
               exdates=exdates, rdates=rdates)
    rid = None
    if "RECURRENCE-ID" in first:
        n, params, value = first["RECURRENCE-ID"]
        rid = _instant(n, "RECURRENCE-ID", params, value)
    cancelled = "STATUS" in first and first["STATUS"][2].strip().upper() == "CANCELLED"
    return ev, rid, cancelled, has_duration, "SUMMARY" in first


def read_calendar(text):
    events, overrides = [], []
    begin, props, depth = None, [], 0
    for n, line in _lines(text):
        upper = line.strip().upper()
        if begin is None:
            if upper == "BEGIN:VEVENT":
                begin, props, depth = n, [], 0
            continue
        if upper.startswith("BEGIN:"):
            depth += 1
            continue
        if upper.startswith("END:"):
            if depth:
                depth -= 1
                continue
            if upper == "END:VEVENT":
                ev = _event(begin, props)
                (overrides if ev[1] is not None else events).append((begin, ev))
                begin = None
            continue
        if depth:
            continue
        name, params, value = _property(n, line)
        props.append((n, name, params, value))
    if begin is not None:
        raise CalendarError(begin, "the event never ends (no END:VEVENT)")
    by_uid = {}
    for _, (ev, *_rest) in events:
        by_uid.setdefault(ev.uid, ev)
    for begin, (ov, rid, cancelled, has_duration, has_summary) in overrides:
        main = by_uid.get(ov.uid)
        if main is None:
            raise CalendarError(begin, f"RECURRENCE-ID for an event that isn't here: {ov.uid}")
        if cancelled:
            main.overrides[rid] = None
            continue
        if not has_duration:
            ov.duration = main.duration
        if not has_summary:
            ov.summary = main.summary
        main.overrides[rid] = ov
    return [ev for _, (ev, *_rest) in events]
