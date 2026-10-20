# Plan

Parts and owners:

- business time (`sla/business.py`): whole seconds between two aware datetimes, per region,
  fixed offsets, hours and holidays; calendar seconds for enterprise urgent.
- rules (`sla/rules.py`): priority from field or subject (whole words), starter downgrade,
  duplicate keys and marking (compare with kept tickets only, 48 h inclusive), targets.
- measuring and report (`sla/report.py`): first response and resolution with pauses,
  breaches, ISO weeks in local time, median and nearest-rank p90, half-up rounding.

Shared shape: tickets are dicts with parsed datetimes; times are kept in whole seconds
until a rule rounds them.
