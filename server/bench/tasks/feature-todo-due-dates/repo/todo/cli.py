"""Command line interface for the todo list."""

import argparse
import sys

from . import store


def find_task(data, task_id):
    for task in data["tasks"]:
        if task["id"] == task_id:
            return task
    return None


def not_found(task_id):
    print(f"error: no task with id {task_id}", file=sys.stderr)
    return 1


def format_task(task):
    mark = "x" if task["done"] else " "
    return f"[{mark}] {task['id']} {task['text']}"


def cmd_add(args, data):
    task = {"id": data["next_id"], "text": args.text, "done": False}
    data["tasks"].append(task)
    data["next_id"] += 1
    print(f"Added {task['id']}: {task['text']}")
    return 0


def cmd_list(args, data):
    tasks = data["tasks"]
    if args.pending:
        tasks = [t for t in tasks if not t["done"]]
    for task in sorted(tasks, key=lambda t: t["id"]):
        print(format_task(task))
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


def build_parser():
    parser = argparse.ArgumentParser(prog="todo")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("add", help="add a task")
    p.add_argument("text")
    p.set_defaults(func=cmd_add, saves=True)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--pending", action="store_true", help="only tasks that are not done")
    p.set_defaults(func=cmd_list, saves=False)

    p = sub.add_parser("done", help="mark a task as done")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_done, saves=True)

    p = sub.add_parser("remove", help="remove a task")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_remove, saves=True)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    data = store.load()
    code = args.func(args, data)
    if code == 0 and args.saves:
        store.save(data)
    return code
