"""Expanding a rule into instants (RECUR.md section 3)."""

import calendar
from datetime import date, datetime, timedelta, timezone

from calrule.localtime import localize, utc_of

HORIZON_DAYS = 146097  # 400 years


def _month_days(y, m):
    return calendar.monthrange(y, m)[1]


def _monthdays(rule, y, m):
    n = _month_days(y, m)
    out = set()
    for md in rule.bymonthday:
        d = md if md > 0 else n + md + 1
        if 1 <= d <= n:
            out.add(date(y, m, d))
    return out


def _weekdays_in(days, rule):
    """The days of `days` (in order) that BYDAY picks, ordinals counted within `days`."""
    out = set()
    for ordinal, wd in rule.byday:
        same = [d for d in days if d.weekday() == wd]
        if ordinal is None:
            out.update(same)
        elif -len(same) <= ordinal <= len(same) and ordinal:
            out.add(same[ordinal - 1] if ordinal > 0 else same[ordinal])
    return out


def _month(y, m):
    return [date(y, m, d) for d in range(1, _month_days(y, m) + 1)]


def _in_month(rule, y, m, first):
    if rule.bymonth and m not in rule.bymonth:
        return set()
    if not rule.bymonthday and not rule.byday:
        return {date(y, m, first.day)} if first.day <= _month_days(y, m) else set()
    sets = []
    if rule.bymonthday:
        sets.append(_monthdays(rule, y, m))
    if rule.byday:
        sets.append(_weekdays_in(_month(y, m), rule))
    return set.intersection(*sets)


def _in_year(rule, y, first):
    if not rule.bymonthday and not rule.byday:
        months = rule.bymonth or (first.month,)
        return {date(y, m, first.day) for m in months if first.day <= _month_days(y, m)}
    months = rule.bymonth or tuple(range(1, 13))
    sets = []
    if rule.bymonthday:
        sets.append(set().union(*(_monthdays(rule, y, m) for m in months)))
    if rule.byday:
        if rule.bymonth:
            sets.append(set().union(*(_weekdays_in(_month(y, m), rule) for m in months)))
        else:
            year = [date(y, 1, 1) + timedelta(days=i) for i in range(366 if calendar.isleap(y) else 365)]
            sets.append(_weekdays_in(year, rule))
    return set.intersection(*sets)


def _matches(rule, d):
    if rule.bymonth and d.month not in rule.bymonth:
        return False
    if rule.bymonthday and d not in _monthdays(rule, d.year, d.month):
        return False
    return not rule.byday or d.weekday() in {wd for _, wd in rule.byday}


def _setpos(rule, days):
    days = sorted(days)
    if not rule.bysetpos:
        return days
    picked = set()
    for p in rule.bysetpos:
        if p > 0 and p <= len(days):
            picked.add(days[p - 1])
        elif p < 0 and -p <= len(days):
            picked.add(days[p])
    return sorted(picked)


def rule_dates(rule, first):
    """The rule's dates from `first`'s period on, in order (dates before `first` included)."""
    end = first + timedelta(days=HORIZON_DAYS)
    k = 0
    while True:
        step = k * rule.interval
        if rule.freq == "DAILY":
            p = first + timedelta(days=step)
            if p > end:
                return
            days = [p] if _matches(rule, p) else []
        elif rule.freq == "WEEKLY":
            p = first - timedelta(days=(first.weekday() - rule.wkst) % 7) + timedelta(weeks=step)
            if p > end:
                return
            wanted = {wd for _, wd in rule.byday} or {first.weekday()}
            days = [p + timedelta(days=i) for i in range(7)
                    if (p + timedelta(days=i)).weekday() in wanted
                    and (not rule.bymonth or (p + timedelta(days=i)).month in rule.bymonth)]
        elif rule.freq == "MONTHLY":
            y, m = divmod(first.month - 1 + step, 12)
            y, m = first.year + y, m + 1
            if date(y, m, 1) > end:
                return
            days = _in_month(rule, y, m, first)
        else:
            y = first.year + step
            if date(y, 1, 1) > end:
                return
            days = _in_year(rule, y, first)
        yield from _setpos(rule, days)
        k += 1


def _utc(aware):
    return utc_of(aware)


def occurrences(rule, start, zone, *, exdates=(), rdates=(), after=None, limit=None):
    excluded = {_utc(x) for x in exdates}
    extra = sorted({_utc(r): r for r in rdates}.items())
    after_utc = _utc(after) if after is not None else None

    def from_rule():
        first = localize(start, zone)
        yield first
        if rule is None:
            return
        n = 1
        until = rule.until
        for d in rule_dates(rule, start.date()):
            if d <= start.date():
                continue
            if rule.count is not None and n >= rule.count:
                return
            inst = localize(datetime.combine(d, start.time()), zone)
            if isinstance(until, datetime):
                if inst.astimezone(timezone.utc) > until:
                    return
            elif until is not None and d > until:
                return
            n += 1
            yield inst

    def merged():
        i = 0
        for inst in from_rule():
            u = _utc(inst)
            while i < len(extra) and extra[i][0] < u:
                yield extra[i]
                i += 1
            if i < len(extra) and extra[i][0] == u:
                i += 1
            yield u, inst
        yield from extra[i:]

    given = 0
    for u, inst in merged():
        if u in excluded or (after_utc is not None and u <= after_utc):
            continue
        if limit is not None and given >= limit:
            return
        given += 1
        yield inst
