# Support SLA policy (v7)

How Support measures its service levels. `python -m sla <data_dir>` must produce the
report below from an export in `data_dir`; the leadership dashboard reads it, and the
numbers must match this policy exactly.

## 1. The export

- `tickets.jsonl`: one ticket per line:
  `{"id", "customer", "subject", "priority" (may be missing or null), "created", "events"}`.
  `created` and every event's `at` are ISO 8601 times with an offset. Events are in time
  order: `{"type": "first_response", "at"}` or `{"type": "status", "status": "open" |
  "pending_customer" | "solved", "at"}`.
- `customers.json`: `{"<customer>": {"plan": "enterprise" | "business" | "starter",
  "region": "Americas" | "EMEA" | "APAC"}}`. A customer that isn't listed is a `starter`
  customer in `Americas`.
- `as_of.txt`: the time the export was taken (ISO 8601 with an offset).

## 2. Business time

Each region's team works Monday to Friday, in its own local time, which is a fixed offset
from UTC all year (no daylight saving):

| region | local time | hours |
| --- | --- | --- |
| Americas | UTC−05:00 | 08:00 to 17:00 |
| EMEA | UTC+01:00 | 09:00 to 17:30 |
| APAC | UTC+08:00 | 09:00 to 18:00 |

Holidays (local dates, no business hours at all):

- Americas: 2026-01-01, 2026-01-19, 2026-02-16, 2026-05-25, 2026-07-03, 2026-09-07.
- EMEA: 2026-01-01, 2026-04-03, 2026-04-06, 2026-05-01, 2026-05-14, 2026-05-25.
- APAC: 2026-01-01, 2026-02-17, 2026-02-18, 2026-04-03, 2026-05-01, 2026-05-31.

The **business minutes** between two times are the minutes of the region's business hours
that fall between them. They can be fractional; keep the fractions until a rule says to
round.

`sla.business.minutes(start, end, region)` returns them (`start` and `end` are aware
`datetime`s; 0 when `end` is not after `start`).

## 3. Priority

A ticket's priority is its `priority` field when that is one of `urgent`, `high`, `normal`,
`low`. Otherwise it comes from the subject, checking these lists in this order. The words
match whole words, in any case:

1. `urgent`: outage, down, breach, "cannot log in", "can't log in"
2. `high`: error, failed, failing, broken
3. `low`: question, "how do i", "feature request"
4. otherwise `normal`.

`starter` customers have no `urgent` tickets: theirs become `high` (whether the priority was
given or derived).

`sla.rules.priority(ticket, plan)` returns it (`ticket` is a dict as in the export).

## 4. Duplicates

A ticket's **key** is its subject in lower case, with any `re:`, `fw:` or `fwd:` at its start
removed (repeatedly, with the spaces after them), runs of whitespace made one space, and
leading and trailing spaces and the characters `.!?` removed from both ends.

Go through the tickets in order of `created` (then `id`). A ticket is a **duplicate** when an
earlier ticket that is not itself a duplicate has the same customer and key and was created
at most 48 hours (calendar) before it. Duplicates are counted, and otherwise ignored.

`sla.rules.key(subject)` returns the key.

## 5. Targets

Hours of business time:

| plan | priority | first response | resolution |
| --- | --- | ---: | ---: |
| enterprise | urgent | 1 | 8 |
| enterprise | high | 2 | 16 |
| enterprise | normal | 4 | 40 |
| enterprise | low | 8 | 80 |
| business | urgent | 2 | 16 |
| business | high | 4 | 24 |
| business | normal | 8 | 48 |
| business | low | 16 | 96 |
| starter | high | 8 | 40 |
| starter | normal | 24 | 80 |
| starter | low | 48 | 160 |

**Enterprise urgent** tickets are measured in **calendar** time (24 × 7, no holidays), every
other ticket in its region's business time.

## 6. Measuring a ticket

- **First response**: the time from `created` to the first `first_response` event. A ticket
  without one is measured up to `as_of` and is *unanswered*.
- **Resolution**: the time from `created` to the ticket's **last** `solved` status, if the
  ticket's last status is `solved`. Otherwise it is measured up to `as_of` and is
  *unresolved*. Time while the ticket is `pending_customer` doesn't count: a pause starts at
  a `pending_customer` status and ends at the next status event (or at `as_of`, or at the end
  of the measurement, whichever comes first).
- A measure **breaches** when its time is greater than its target (in minutes). An
  unanswered or unresolved ticket breaches only once its time up to `as_of` is already over
  the target.

## 7. The report

`python -m sla <data_dir>` writes `out/report.json` (relative to where it runs), and
`sla.report.build(data_dir)` returns the same dict:

```json
{"as_of": "...", "weeks": [...], "breaches": [...]}
```

- `as_of`: as in `as_of.txt`, unchanged.
- `weeks`: one entry per region and ISO week with tickets, sorted by week, then region in
  the order Americas, EMEA, APAC:
  `{"region", "week" ("2026-W09"), "tickets", "duplicates", "first_response_breaches",
  "resolution_breaches", "median_first_response_minutes", "p90_resolution_minutes"}`.
  - A ticket belongs to the ISO week of its `created` time in its region's local time.
  - `tickets` counts tickets that aren't duplicates; `duplicates` counts duplicates.
  - The breach counts count breaching (non-duplicate) tickets.
  - `median_first_response_minutes`: the median first response time of the answered
    tickets (the mean of the two middle values when there is an even number), rounded half
    up to a whole number; `null` when none was answered.
  - `p90_resolution_minutes`: the 90th percentile of the resolution times of the resolved
    tickets, by nearest rank (sort them; take the value at position ⌈0.9 × n⌉, counting from
    1), rounded half up to a whole number; `null` when none was resolved.
- `breaches`: every breach, sorted by ticket id then kind: `{"ticket", "kind"
  ("first_response" | "resolution"), "plan", "priority", "minutes_over"}`, where
  `minutes_over` is the time minus the target, rounded half up to a whole number.
