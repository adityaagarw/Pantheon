# todo

A small command line todo list.

## Commands

```
python -m todo add "Buy milk"                  # Added 1: Buy milk
python -m todo add "Pay rent" --due 2026-11-01 # add a task with a due date
python -m todo list                            # show all tasks
python -m todo list --pending                  # show only tasks that are not done
python -m todo list --overdue                  # show only overdue tasks
python -m todo list --sort due                 # earliest due date first, no due date last
python -m todo done 1                          # Done 1: Buy milk
python -m todo due 1 2026-12-24                # set or change a due date
python -m todo due 1 --clear                   # remove a due date
python -m todo remove 1                        # Removed 1: Buy milk
```

`list` prints one line for each task:

```
[ ] 1 Buy milk
[ ] 2 Pay rent (due 2026-11-01)
[ ] 3 File taxes (due 2026-04-30, OVERDUE)
[x] 4 Call the bank
```

A task is overdue when it is not done and its due date is before today.
Dates use the format `YYYY-MM-DD`. Set `TODO_TODAY` to use a fixed date as today.

## Data file

Tasks are stored in JSON. The default file is `~/.todo.json`.
Set the environment variable `TODO_FILE` to use another file.
Each task has a `"due"` key with a `"YYYY-MM-DD"` string or `null`.

## Exit codes

- 0: success
- 1: no task with the given id
- 2: wrong command line arguments or an invalid date
