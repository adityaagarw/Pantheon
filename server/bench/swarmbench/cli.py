"""SwarmBench command line."""

import argparse
import sys
import tempfile
from pathlib import Path

from .grader import grade_task
from .prompt import build_prompt
from .report import render_compare, summarize, write_report
from .runner import load_harness, regrade, run_benchmark
from .tasks import load_tasks
from .utils import copy_tree
from .workflows import list_workflows, load_workflow, resolve_workflow


def cmd_list(args):
    tasks = load_tasks(args.tasks)
    print(f"{'ID':32} {'CATEGORY':14} {'LEVEL':8} {'WORKFLOW':24} TITLE")
    for t in tasks:
        print(f"{t.id:32} {t.category:14} {t.difficulty:8} {t.workflow:24} {t.title}")
    print(f"\n{len(tasks)} task(s).")


def cmd_show(args):
    task = load_tasks(args.task)[0]
    print(build_prompt(task, resolve_workflow(args.workflow, task), "<work folder>"))


def cmd_workflows(args):
    if args.name:
        print(load_workflow(args.name).to_markdown())
        return
    for name in list_workflows():
        print(f"{name:26} {load_workflow(name).summary}")


def cmd_run(args):
    harness = load_harness(args.harness)
    tasks = load_tasks(args.tasks)
    run_benchmark(tasks, harness, workflow_mode=args.workflow, repeat=args.repeat, run_id=args.run_id,
                  workspace_root=args.workspace_root, timeout_minutes=args.timeout_minutes,
                  jobs=args.jobs)


def cmd_grade(args):
    print(f"Report: {regrade(args.run_dir, args.task)}")


def cmd_report(args):
    path = write_report(args.run_dir)
    print(path.read_text(encoding="utf-8"))


def cmd_compare(args):
    if args.json:
        import json

        text = json.dumps([summarize(d) for d in args.run_dirs], indent=2)
    else:
        text = render_compare(args.run_dirs)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)


def cmd_validate(args):
    """Check that each task is fair: the start state fails and the reference solution passes."""
    problems = 0
    print(f"{'TASK':32} {'START':>7} {'SCORE':>6} {'SOLUTION':>9} {'SCORE':>6}  NOTES")
    for task in load_tasks(args.tasks):
        notes = []
        with tempfile.TemporaryDirectory(prefix="swb-validate-") as tmp:
            start_ws = Path(tmp) / "start"
            copy_tree(task.repo_path, start_ws)
            start = grade_task(task, start_ws, None)
            solved_ws = Path(tmp) / "solved"
            copy_tree(task.repo_path, solved_ws)
            if task.solution_path.exists():
                copy_tree(task.solution_path, solved_ws)
            else:
                notes.append("no solution/ folder")
            solved = grade_task(task, solved_ws, None)
        start_hidden = start["parts"]["hidden_tests"]
        if start_hidden["score"] >= 1.0:
            notes.append("start state already passes all hidden tests")
        if start_hidden["total"] == 0:
            notes.append("no hidden tests")
        for name, part in solved["parts"].items():
            if part is not None and part["score"] < 1.0 and name in solved["weights"]:
                detail = part.get("failed") or part.get("changed") or part.get("note") or part.get("error") or ""
                notes.append(f"solution {name}={part['score']:.2f} {detail}")
        problems += bool(notes)
        sh = solved["parts"]["hidden_tests"]
        print(f"{task.id:32} {start_hidden['passed']:>3}/{start_hidden['total']:<3} {start['score']:>6.1f} "
              f"{sh['passed']:>4}/{sh['total']:<4} {solved['score']:>6.1f}  "
              + ("; ".join(str(n) for n in notes) if notes else "ok"))
    print("\nAll tasks valid." if not problems else f"\n{problems} task(s) have problems.")
    return 1 if problems else 0


def build_parser():
    parser = argparse.ArgumentParser(prog="swarmbench", description="Benchmark agent swarms and harnesses on real work tasks.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="list tasks")
    p.add_argument("--tasks", default="all", help="'all', task ids, or category:NAME (comma list)")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="print the prompt of a task")
    p.add_argument("task")
    p.add_argument("--workflow", default="task", help="'task' (default), 'none', or a workflow name")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("workflows", help="list workflows, or show one")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_workflows)

    p = sub.add_parser("run", help="run a harness on tasks")
    p.add_argument("--harness", required=True, help="harness name in harnesses/ or a path to a JSON file")
    p.add_argument("--tasks", default="all")
    p.add_argument("--workflow", default="task", help="'task' (default), 'none', or a workflow name")
    p.add_argument("--repeat", type=int, default=1, help="run each task N times to see variance")
    p.add_argument("--run-id")
    p.add_argument("--workspace-root", help="where work folders go (default ~/.swarmbench/workspaces)")
    p.add_argument("--timeout-minutes", type=float, help="kill the harness after this time (default 2x task budget)")
    p.add_argument("--jobs", type=int, default=1, help="run this many tasks at once (command and pantheon harnesses)")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("grade", help="grade the work folders of a run again")
    p.add_argument("run_dir")
    p.add_argument("--task", action="append", help="task key to grade (can repeat)")
    p.set_defaults(func=cmd_grade)

    p = sub.add_parser("report", help="make the report of a run")
    p.add_argument("run_dir")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("compare", help="compare two or more runs")
    p.add_argument("run_dirs", nargs="+")
    p.add_argument("--out", help="also write the comparison to this file")
    p.add_argument("--json", action="store_true", help="print the runs' summaries as JSON")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("validate", help="check that tasks are fair and solvable")
    p.add_argument("--tasks", default="all")
    p.set_defaults(func=cmd_validate)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    return args.func(args) or 0
