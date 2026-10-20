# calrule: recurring events

`calrule` reads calendar files, expands recurring events and prints an agenda. It follows
iCalendar (RFC 5545) where this document says so and this document where they differ.
Standard library only (Python 3.10+). `calrule/zones.py` and `calrule/errors.py` are given.

Weekdays are numbered 0 = Monday to 6 = Sunday and written `MO TU WE TH FR SA SU`.

## 1. Local time (`calrule/localtime.py`)

A zone is a name from `calrule.zones.ZONES`; any other name raises `UnknownZone`.

- `to_utc(local, zone)`: a naive local datetime as a naive UTC datetime. The offset is the
  one in force at that instant (`zones.offset_at`). Two cases need care:
  - **A gap** (clocks go forward, so the local time never happens): the time is read with
    the offset in force *before* the gap. New York, 2025-03-09 02:30 → 07:30 UTC.
  - **An overlap** (clocks go back, so the local time happens twice): the *first* of the
    two instants. New York, 2025-11-02 01:30 → 05:30 UTC (not 06:30).
- `from_utc(utc, zone)`: a naive UTC datetime as an aware datetime in the zone, its tzinfo a
  fixed `datetime.timezone(offset)` with the offset in force then.
- `localize(local, zone)`: `from_utc(to_utc(local, zone), zone)`. So a gap time moves
  forward: New York 2025-03-09 02:30 becomes 03:30-04:00.

## 2. Rules (`calrule/rule.py`)

`parse_rule(text)` reads a recurrence rule into a `Rule`; `format_rule(rule)` writes one.

Text is `NAME=VALUE` parts joined by `;`, in any order, names and values in any case, with
an optional `RRULE:` in front. Parts:

| Part | Value | Default |
| --- | --- | --- |
| `FREQ` | `DAILY`, `WEEKLY`, `MONTHLY` or `YEARLY` (required) | |
| `INTERVAL` | a positive integer | 1 |
| `COUNT` | a positive integer | none |
| `UNTIL` | a date `YYYYMMDD`, or a UTC time `YYYYMMDDTHHMMSSZ` | none |
| `BYDAY` | weekdays, each with an optional signed ordinal: `MO`, `2TU`, `-1FR`, `+3WE` | none |
| `BYMONTHDAY` | days 1 to 31 or -31 to -1 | none |
| `BYMONTH` | months 1 to 12 | none |
| `BYSETPOS` | positions 1 to 366 or -366 to -1 | none |
| `WKST` | a weekday | `MO` |

Lists are comma-separated. `Rule` is a dataclass with these fields, in the order written in
the text for lists:

```python
Rule(freq="MONTHLY", interval=1, count=None, until=None,
     byday=((2, 1),),          # (ordinal or None, weekday): 2TU
     bymonthday=(), bymonth=(), bysetpos=(), wkst=0)
```

`until` is a `date`, or an aware `datetime` in UTC (`tzinfo=timezone.utc`).

`RuleError` (a `ValueError`) is raised, its message starting with the part's name, for: a
missing `FREQ`; an unknown part name or a part given twice; a value out of range or not a
number; `COUNT` and `UNTIL` together; an `UNTIL` time without the `Z`; a `BYDAY` ordinal
of 0 or beyond ±53, or any ordinal unless `FREQ` is `MONTHLY` or `YEARLY` (message starts
`BYDAY`); `BYMONTHDAY` with `FREQ=WEEKLY` (starts `BYMONTHDAY`); `BYSETPOS` without
another `BY...` part (starts `BYSETPOS`).

`format_rule` writes `FREQ` first, then `INTERVAL` (only when not 1), `COUNT`, `UNTIL`,
`BYMONTH`, `BYMONTHDAY`, `BYDAY`, `BYSETPOS`, `WKST` (only when not `MO`), upper case,
lists in the rule's order, ordinals without `+`, and no `RRULE:` in front:
`FREQ=MONTHLY;INTERVAL=2;COUNT=6;BYDAY=2TU,-1FR`.

## 3. Expanding (`calrule/expand.py`)

```python
occurrences(rule, start, zone, *, exdates=(), rdates=(), after=None, limit=None)
```

yields the event's start instants, in order, as aware datetimes from `localize`. `start`
is a naive local datetime; `rule` may be `None` (a single event). `exdates`, `rdates` and
`after` are aware datetimes.

1. **Wall-clock time.** Every occurrence is at `start`'s local time of day on its date, in
   the zone, through `localize`: a daily 09:00 stays at 09:00 across a change of clocks, and
   a time in a gap moves forward as section 1 says.
2. **The start comes first.** `start` is always the first occurrence, even when it doesn't
   match the rule, and it counts toward `COUNT`.
3. **Periods.** The rule steps `INTERVAL` periods at a time from the period that contains
   `start`: the day; the week, beginning on `WKST`, that contains it; its month; its year.
4. **Dates in a period.**
   - `DAILY`: the day itself. `BYMONTH`, `BYMONTHDAY` and `BYDAY` only filter: the day is
     kept when it matches every one given.
   - `WEEKLY`: the days of the week with a `BYDAY` weekday (default: `start`'s weekday).
     `BYMONTH` filters.
   - `MONTHLY` (only months in `BYMONTH`, when given): the `BYMONTHDAY` days (-1 is the
     last day; a day the month doesn't have is skipped, never moved to the month's end) and
     the `BYDAY` days of the month (`2TU` the second Tuesday, `-1FR` the last Friday, `MO`
     every Monday). With both given, a day must match both. With neither, `start`'s day of
     the month; months without that day are skipped.
   - `YEARLY`: `BYMONTHDAY` days in each month of `BYMONTH` (all twelve when not given).
     `BYDAY` days count within each `BYMONTH` month when `BYMONTH` is given, and within the
     whole year when it isn't (`20MO` is the year's 20th Monday). With both given, a day
     must match both. With neither, `start`'s day of the month in each `BYMONTH` month, or
     `start`'s month and day when there's no `BYMONTH` (29 February: leap years only).
5. **`BYSETPOS`** keeps, from a period's dates in order, the ones at those positions (1 the
   first, -1 the last); positions beyond the dates are skipped; no date twice.
6. Dates before `start`'s date are dropped (the first period can begin before it).
7. **`COUNT`** stops after that many occurrences, `start` included. **`UNTIL`**: a date
   keeps occurrences whose local date is on or before it; a UTC time keeps those at or
   before that instant. `start` itself is kept either way.
8. **`EXDATE`s** remove the occurrences at those instants. They still count toward
   `COUNT`: `COUNT=5` with one excluded gives 4.
9. **`RDATE`s** add instants, in order among the rest; one at the same instant as an
   occurrence appears once. They don't count toward `COUNT` and `UNTIL` doesn't limit them.
   An `EXDATE` removes an `RDATE` at its instant too.
10. **`after`**: only instants strictly after it. **`limit`**: at most that many.
11. A rule that never matches again (`FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30`) ends: look no
    further than 400 years after `start`.

## 4. Calendar files (`calrule/ics.py`)

`read_calendar(text)` returns the `Event`s of a calendar file, in the file's order of their
main `VEVENT`s:

```python
Event(uid, summary, zone, start, duration, rule, exdates, rdates, overrides)
```

`start` is the naive local start, `zone` its zone name, `duration` a `timedelta`, `rule` a
`Rule` or `None`, `exdates` and `rdates` lists of aware datetimes, `overrides` a dict from
an instance's original start (aware) to the `Event` that replaces it, or to `None` when
that instance is cancelled.

- **Lines** end with CRLF or LF. A line that begins with a space or a tab continues the
  line before: remove the line break and that one character. Blank lines are skipped.
- **A property** is `NAME;PARAM=VALUE;...:VALUE`. Names and parameter names are in any
  case. The value is everything after the first `:` that isn't inside a double-quoted
  parameter value.
- Only `BEGIN:VEVENT` ... `END:VEVENT` blocks matter; everything outside them, and anything
  nested inside one (a `VALARM`), is skipped.
- **Times**: `20250310T090000` with a `TZID` parameter is local time in that zone;
  `20250310T090000Z` is UTC (zone `UTC`). A time with neither, or a date-only value
  (`VALUE=DATE`), is an error.
- `UID` and `DTSTART` are required. `SUMMARY` is text: `\n` or `\N` is a line break, and
  `\,` `\;` `\\` are `,` `;` `\`; missing, it's "".
- `DTEND` (the duration is the time between the two instants; before `DTSTART` is an
  error) or `DURATION` (`P1W`, `P2D`, `PT1H30M`, `P1DT12H`, `PT45S`; nothing negative).
  Neither: no duration.
- `RRULE` (section 2). `EXDATE` and `RDATE`: comma-separated times, all in the line's
  `TZID` or all in UTC, on any number of lines.
- A `VEVENT` with a `RECURRENCE-ID` (a time, like `DTSTART`) replaces the instance of the
  event with its `UID` that started at that instant, wherever it is in the file: the
  instance takes the override's `DTSTART`, its duration (the event's when it has neither
  `DTEND` nor `DURATION`) and its `SUMMARY` (the event's when it has none). With
  `STATUS:CANCELLED` the instance is removed instead. An override whose `UID` has no event
  is an error at its `BEGIN:VEVENT` line.
- **Errors** raise `CalendarError(line, message)`: the line number (from 1) of the
  property's first line in the file, or of the `BEGIN:VEVENT` line for a missing `UID` or
  `DTSTART` and for a `VEVENT` that never ends. An unknown `TZID` and a rule `parse_rule`
  refuses are errors too, the message saying why (the `RuleError`'s message as it is).

## 5. Agenda (`calrule/agenda.py`)

`instances(events, start, end)` gives every instance overlapping `[start, end)` (aware
datetimes), as `Instance(uid, summary, start, end)` with aware `start` and `end` in the
instance's own zone, each with the offset the zone has at that instant (an instance that
crosses a clock change ends at a different offset from its start). An instance overlaps when it starts before `end` and ends after
`start`; one with no duration, when `start <= its start < end`. Overrides apply: a moved
instance is judged at its new time, wherever its old one was. Sorted by start instant, then
by `uid`.

`agenda(events, start, end, zone)` prints those instances in `zone` as text, one block per
local date that has an instance starting on it, blocks in order and separated by one blank
line:

```
Mon 2025-03-10
  09:00-09:15  Standup
  13:00  Pay day reminder
  23:30-00:30+1  Release window
```

A heading is the weekday's first three letters and the ISO date. An instance is two spaces,
`HH:MM-HH:MM` local start and end (`+N` after the end when it is N dates later; just
`HH:MM` with no duration), two spaces and its summary (line breaks as spaces; `(no title)`
when empty). Every line ends with a newline. With no instances: `No events.` and a newline.

## 6. The command line (`calrule/cli.py`, `python -m calrule`)

```
python -m calrule FILE --from YYYY-MM-DD --to YYYY-MM-DD [--tz ZONE]
```

prints `agenda` for `[--from 00:00, --to 00:00)` in `--tz` (default `UTC`), both dates
local in that zone, and exits 0. `main(argv)` in `calrule/cli.py` does it and returns the
exit code. On any problem it prints one line on standard error and returns 2:
`error: FILE: line N: message` for a `CalendarError`, `error: cannot read FILE` for a file
that can't be read, `error: unknown time zone ZONE`, and `error: --to must be after --from`.
