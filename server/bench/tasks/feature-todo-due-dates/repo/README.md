# todo

A small command line todo list.

## Commands

```
python -m todo add "Buy milk"      # Added 1: Buy milk
python -m todo list                # show all tasks
python -m todo list --pending      # show only tasks that are not done
python -m todo done 1              # Done 1: Buy milk
python -m todo remove 1            # Removed 1: Buy milk
```

`list` prints one line for each task:

```
[ ] 1 Buy milk
[x] 2 Call the bank
```

## Data file

Tasks are stored in JSON. The default file is `~/.todo.json`.
Set the environment variable `TODO_FILE` to use another file.

## Exit codes

- 0: success
- 1: no task with the given id
- 2: wrong command line arguments
