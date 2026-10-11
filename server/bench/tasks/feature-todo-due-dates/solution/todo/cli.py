"""Command line interface for the todo list."""

import argparse
import os
import sys
from datetime import date

from . import store


def today():
    value = os.environ.get("TODO_TODAY")
    return date.fromisoformat(value) if value else date.today()


def parse_due(value):
    """Return a YYYY-MM-DD string, or None if the value is not a valid date."""
    if len(value) != 10:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def invalid_date(value):
    print(f"error: invalid date: {value} (use YYYY-MM-DD)", file=sys.stderr)
    return 2


def find_task(data, task_id):
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def not_found(task_id):
    print(f"error: no task with id {task_id}", file=sys.stderr)
    return 1


def is_overdue(task, now):
    due = task.get("due")
    return bool(due) and not task["done"] and date.fromisoformat(due) < now


def format_task(task, now):
    mark = "x" if task["done"] else " "
    line = f"[{mark}] {task['id']} {task['text']}"
    if task.get("due"):
        suffix = ", OVERDUE" if is_overdue(task, now) else ""
        line += f" (due {task['due']}{suffix})"
    return line


def cmd_add(args, data):
    due = None
    if args.due is not None:
        due = parse_due(args.due)
        if due is None:
            return invalid_date(args.due)
    task = {"id": data["next_id"], "text": args.text, "done": False, "due": due}
    data["tasks"].append(task)
    data["next_id"] += 1
    print(f"Added {task['id']}: {task['text']}")
    return 0


def cmd_list(args, data):
    now = today()
    tasks = data["tasks"]
    if args.pending:
        tasks = [t for t in tasks if not t["done"]]
    if args.overdue:
        tasks = [t for t in tasks if is_overdue(t, now)]
    if args.sort == "due":
        tasks = sorted(tasks, key=lambda t: (t.get("due") is None, t.get("due") or "", t["id"]))
    else:
        tasks = sorted(tasks, key=lambda t: t["id"])
    for task in tasks:
        print(format_task(task, now))
    return 0


def cmd_done(args, data):
    task = find_task(data, args.id)
    if task is None:
        return not_found(args.id)
    task["done"] = True
    print(f"Done {task['id']}: {task['text']}")
    return 0


def cmd_remove(args, data):
    task = find_task(data, args.id)
    if task is None:
        return not_found(args.id)
    data["tasks"].remove(task)
    print(f"Removed {task['id']}: {task['text']}")
    return 0


def cmd_due(args, data):
    if args.clear == (args.date is not None):
        print("error: give a date or --clear", file=sys.stderr)
        return 2
    due = None
    if args.date is not None:
        due = parse_due(args.date)
        if due is None:
            return invalid_date(args.date)
    task = find_task(data, args.id)
    if task is None:
        return not_found(args.id)
    task["due"] = due
    print(f"Updated {task['id']}: " + (f"due {due}" if due else "no due date"))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="todo")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="add a task")
    p.add_argument("text")
    p.add_argument("--due", help="due date, YYYY-MM-DD")
    p.set_defaults(func=cmd_add, saves=True)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--pending", action="store_true", help="only tasks that are not done")
    p.add_argument("--overdue", action="store_true", help="only overdue tasks")
    p.add_argument("--sort", choices=["id", "due"], default="id", help="sort order")
    p.set_defaults(func=cmd_list, saves=False)

    p = sub.add_parser("done", help="mark a task as done")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_done, saves=True)

    p = sub.add_parser("remove", help="remove a task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_remove, saves=True)

    p = sub.add_parser("due", help="set or clear the due date of a task")
    p.add_argument("id", type=int)
    p.add_argument("date", nargs="?", help="due date, YYYY-MM-DD")
    p.add_argument("--clear", action="store_true", help="remove the due date")
    p.set_defaults(func=cmd_due, saves=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data = store.load()
    code = args.func(args, data)
    if code == 0 and args.saves:
        store.save(data)
    return code
