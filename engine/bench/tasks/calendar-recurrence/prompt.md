Build `calrule` as specified in `RECUR.md`: local time in a zone across clock changes,
recurrence rules (reading and writing them), expanding a rule into instants, reading
calendar files with overrides, the agenda, and the `python -m calrule` command line.
`calrule/zones.py` and `calrule/errors.py` are done.

There isn't much code, but every sentence of the spec is a rule the calendar team relies
on, and they check each one: times in a gap or an overlap, which day a rule picks in each
period, what counts toward `COUNT`, how exclusions and extra dates combine, how a file's
lines are read, which instances a window shows, and the agenda character for character.

The parts are separate (local time; rules; expanding; calendar files; the agenda and the
command line) and meet at the shapes the spec gives. Add tests in `tests/` (unittest) for
each part. Don't change `RECUR.md`, `calrule/zones.py` or `calrule/errors.py`.
