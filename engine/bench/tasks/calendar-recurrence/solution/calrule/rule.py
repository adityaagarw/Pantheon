"""Recurrence rules (RECUR.md section 2)."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from calrule.errors import RuleError

DAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")


@dataclass(frozen=True)
class Rule:
    freq: str
    interval: int = 1
    count: int = None
    until: object = None
    byday: tuple = ()
    bymonthday: tuple = ()
    bymonth: tuple = ()
    bysetpos: tuple = ()
    wkst: int = 0


def _int(part, value, lo, hi, signed=False):
    try:
        if not re.fullmatch(r"[+-]?\d+" if signed else r"\+?\d+", value):
            raise ValueError
        n = int(value)
    except ValueError:
        raise RuleError(f"{part} must be a number: {value!r}") from None
    if not (lo <= abs(n) <= hi) or (not signed and n < lo):
        raise RuleError(f"{part} is out of range: {value}")
    return n


def _list(part, value, lo, hi):
    return tuple(_int(part, v, lo, hi, signed=True) for v in value.split(","))


def _day(part, value):
    if value not in DAYS:
        raise RuleError(f"{part} has an unknown weekday: {value!r}")
    return DAYS.index(value)


def _until(value):
    if re.fullmatch(r"\d{8}", value):
        try:
            return date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError:
            raise RuleError(f"UNTIL is not a date: {value}") from None
    m = re.fullmatch(r"(\d{8}T\d{6})(Z?)", value)
    if not m:
        raise RuleError(f"UNTIL must be a date or a UTC time: {value}")
    if not m.group(2):
        raise RuleError(f"UNTIL must be in UTC, ending in Z: {value}")
    try:
        return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise RuleError(f"UNTIL is not a time: {value}") from None


def parse_rule(text):
    text = text.strip()
    if text.upper().startswith("RRULE:"):
        text = text[6:]
    parts = {}
    for item in text.split(";"):
        if not item.strip():
            continue
        name, sep, value = item.partition("=")
        name, value = name.strip().upper(), value.strip().upper()
        if not sep or not value:
            raise RuleError(f"{name or item} needs a value")
        if name in parts:
            raise RuleError(f"{name} is given twice")
        parts[name] = value
    known = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "BYDAY", "BYMONTHDAY", "BYMONTH",
             "BYSETPOS", "WKST"}
    for name in parts:
        if name not in known:
            raise RuleError(f"{name} is not a rule part")
    if "FREQ" not in parts:
        raise RuleError("FREQ is required")
    freq = parts["FREQ"]
    if freq not in FREQS:
        raise RuleError(f"FREQ must be one of {', '.join(FREQS)}: {freq}")
    if "COUNT" in parts and "UNTIL" in parts:
        raise RuleError("COUNT and UNTIL can't both be given")
    byday = []
    for v in parts.get("BYDAY", "").split(",") if "BYDAY" in parts else []:
        m = re.fullmatch(r"([+-]?\d+)?([A-Z]{2})", v)
        if not m:
            raise RuleError(f"BYDAY has an unreadable day: {v!r}")
        ordinal = None
        if m.group(1):
            ordinal = int(m.group(1))
            if ordinal == 0 or abs(ordinal) > 53:
                raise RuleError(f"BYDAY ordinal out of range: {v}")
            if freq not in ("MONTHLY", "YEARLY"):
                raise RuleError(f"BYDAY ordinals need FREQ=MONTHLY or YEARLY: {v}")
        byday.append((ordinal, _day("BYDAY", m.group(2))))
    if "BYMONTHDAY" in parts and freq == "WEEKLY":
        raise RuleError("BYMONTHDAY can't be used with FREQ=WEEKLY")
    rule = Rule(
        freq=freq,
        interval=_int("INTERVAL", parts["INTERVAL"], 1, 10**6) if "INTERVAL" in parts else 1,
        count=_int("COUNT", parts["COUNT"], 1, 10**9) if "COUNT" in parts else None,
        until=_until(parts["UNTIL"]) if "UNTIL" in parts else None,
        byday=tuple(byday),
        bymonthday=_list("BYMONTHDAY", parts["BYMONTHDAY"], 1, 31) if "BYMONTHDAY" in parts else (),
        bymonth=tuple(_int("BYMONTH", v, 1, 12) for v in parts["BYMONTH"].split(","))
        if "BYMONTH" in parts else (),
        bysetpos=_list("BYSETPOS", parts["BYSETPOS"], 1, 366) if "BYSETPOS" in parts else (),
        wkst=_day("WKST", parts["WKST"]) if "WKST" in parts else 0,
    )
    if rule.bysetpos and not (rule.byday or rule.bymonthday or rule.bymonth):
        raise RuleError("BYSETPOS needs another BY part")
    return rule


def _fmt_until(until):
    if isinstance(until, datetime):
        return until.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return until.strftime("%Y%m%d")


def format_rule(rule):
    out = [f"FREQ={rule.freq}"]
    if rule.interval != 1:
        out.append(f"INTERVAL={rule.interval}")
    if rule.count is not None:
        out.append(f"COUNT={rule.count}")
    if rule.until is not None:
        out.append(f"UNTIL={_fmt_until(rule.until)}")
    if rule.bymonth:
        out.append("BYMONTH=" + ",".join(map(str, rule.bymonth)))
    if rule.bymonthday:
        out.append("BYMONTHDAY=" + ",".join(map(str, rule.bymonthday)))
    if rule.byday:
        out.append("BYDAY=" + ",".join(f"{'' if o is None else o}{DAYS[d]}" for o, d in rule.byday))
    if rule.bysetpos:
        out.append("BYSETPOS=" + ",".join(map(str, rule.bysetpos)))
    if rule.wkst:
        out.append(f"WKST={DAYS[rule.wkst]}")
    return ";".join(out)
