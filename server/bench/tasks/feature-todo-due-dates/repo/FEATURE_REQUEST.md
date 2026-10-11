# Feature request: due dates

Users want to give tasks a due date and see tasks that are late.

## 1. Add a task with a due date

```
python -m todo add "Pay rent" --due 2026-11-01
```

- The date format is `YYYY-MM-DD`. It must be a real date.
- A bad date (for example `2026-13-01`, `tomorrow`, `01/11/2026`) must:
  - write `error: invalid date: <value> (use YYYY-MM-DD)` to stderr,
  - exit with code 2,
  - not save anything.
- Store the date in the data file as the key `"due"`, with a `"YYYY-MM-DD"` string.
  Tasks with no due date have `"due": null`.

## 2. Show due dates in `list`

A task with a due date has a suffix:

```
[ ] 1 Pay rent (due 2026-11-01)
```

A task is **overdue** when it is not done and its due date is before today.
An overdue task has this suffix:

```
[ ] 1 Pay rent (due 2026-11-01, OVERDUE)
```

A task that is due today is not overdue. A done task is never overdue.
Tasks with no due date look the same as before.

## 3. New `list` options

- `list --overdue`: show only overdue tasks.
- `list --sort due`: sort by due date, earliest first. Tasks with no due date come last.
  When two tasks have the same due date, the lower id comes first.
- `list --sort id`: sort by id. This is the default.
- The options can be used together with each other and with `--pending`.

## 4. New `due` command

```
python -m todo due 3 2026-12-24     # set or change the due date
python -m todo due 3 --clear        # remove the due date
```

- On success, exit with code 0.
- An unknown id writes `error: no task with id <id>` to stderr and exits with code 1
  (the same as `done` and `remove` do now).
- A bad date gives the same error and exit code as in item 1.

## 5. Old data files

Data files from the old version have no `"due"` key. They must still work.
Treat a missing key as "no due date".

## 6. Today's date

The tests need a fixed date. When the environment variable `TODO_TODAY` is set
(format `YYYY-MM-DD`), use it as today's date. If it is not set, use the real date.
