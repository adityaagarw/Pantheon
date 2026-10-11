"""Run harnesses on tasks and save the results."""

import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .adapters import RunContext, make_adapter
from .grader import grade_task
from .prompt import build_prompt
from .report import write_report
from .tasks import BENCH_DIR, BenchError
from .utils import copy_tree, now_iso, read_json, write_json
from .workflows import resolve_workflow

RUNS_DIR = BENCH_DIR / "runs"
HARNESSES_DIR = BENCH_DIR / "harnesses"
DEFAULT_WORKSPACE_ROOT = Path.home() / ".swarmbench" / "workspaces"


def load_harness(name_or_path):
    path = Path(name_or_path)
    if not path.exists():
        path = HARNESSES_DIR / f"{name_or_path}.json"
    if not path.exists():
        known = ", ".join(sorted(p.stem for p in HARNESSES_DIR.glob("*.json")))
        raise BenchError(f"error: no harness '{name_or_path}'. Known: {known}")
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("name", path.stem)
    return config


def new_run_id(harness_name):
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{harness_name}"


def run_one(task, harness, adapter, run_id, run_dir, key, workflow_mode, workspace_root, timeout_minutes):
    workspace = Path(workspace_root) / run_id / key
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    copy_tree(task.repo_path, workspace)

    task_dir = run_dir / key
    task_dir.mkdir(parents=True, exist_ok=True)
    workflow = resolve_workflow(workflow_mode, task)
    ctx = RunContext(
        task=task,
        run_id=run_id,
        task_key=key,
        workspace=workspace,
        agent_workspace="",
        prompt_file=task_dir / "prompt.md",
        metrics_file=task_dir / "metrics.json",
        log_file=task_dir / "harness.log",
        timeout_seconds=int((timeout_minutes or task.time_limit_minutes * 2) * 60),
    )
    ctx.agent_workspace = ctx.fill(harness.get("agent_workspace", "{workspace}"))
    ctx.prompt_file.write_text(build_prompt(task, workflow, ctx.agent_workspace), encoding="utf-8")

    started = now_iso()
    run_info = adapter.run(ctx)
    grade = grade_task(task, workspace, workflow)
    result = {
        "task_id": task.id,
        "task_key": key,
        "title": task.title,
        "category": task.category,
        "difficulty": task.difficulty,
        "workflow": workflow.name if workflow else None,
        "harness": harness["name"],
        "workspace": str(workspace),
        "started": started,
        "finished": now_iso(),
        "run": run_info,
        "metrics": read_json(ctx.metrics_file, {}),
        **grade,
    }
    write_json(task_dir / "result.json", result)
    return result


def run_benchmark(tasks, harness, workflow_mode="task", repeat=1, run_id=None,
                  workspace_root=None, timeout_minutes=None, jobs=1, on_result=None):
    """Run every task (``repeat`` times each). ``jobs`` > 1 runs that many at once (not for
    manual harnesses). ``on_result(result)`` is called after each task is graded."""
    adapter = make_adapter(harness)
    if jobs > 1 and not getattr(adapter, "parallel_ok", False):
        print(f"Note: the '{harness.get('type')}' harness runs one task at a time; ignoring --jobs.")
        jobs = 1
    run_id = run_id or new_run_id(harness["name"])
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = Path(workspace_root or harness.get("workspace_root") or DEFAULT_WORKSPACE_ROOT).expanduser()
    write_json(run_dir / "run.json", {
        "run_id": run_id,
        "harness": harness,
        "workflow_mode": workflow_mode,
        "repeat": repeat,
        "tasks": [t.id for t in tasks],
        "workspace_root": str(workspace_root),
        "jobs": jobs,
        "started": now_iso(),
    })
    print(f"Run {run_id}: {len(tasks)} task(s) x {repeat}, harness '{harness['name']}', "
          f"workflow '{workflow_mode}'" + (f", {jobs} at a time" if jobs > 1 else ""))
    work = [(task, task.id if repeat == 1 else f"{task.id}__r{number}")
            for task in tasks for number in range(1, repeat + 1)]
    printing = threading.Lock()

    def one(item):
        task, key = item
        with printing:
            print(f"  -> {key} ...", flush=True)
        result = run_one(task, harness, adapter, run_id, run_dir, key, workflow_mode,
                         workspace_root, timeout_minutes)
        hidden = result["parts"]["hidden_tests"]
        with printing:
            print(f"     {key}: score {result['score']:5.1f}  hidden {hidden['passed']}/{hidden['total']}"
                  f"  time {result['run']['wall_seconds']} s"
                  f"{'  TIMEOUT' if result['run']['timed_out'] else ''}", flush=True)
        if on_result is not None:
            on_result(result)
        return result

    if jobs > 1:
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            results = list(pool.map(one, work))
    else:
        results = [one(item) for item in work]
    report_path = write_report(run_dir)
    print(f"Report: {report_path}")
    return run_dir, results


def regrade(run_dir, task_keys=None):
    """Grade the kept work folders of a run again (for example after a grader fix)."""
    from .tasks import load_tasks
    from .workflows import load_workflow

    run_dir = Path(run_dir)
    tasks = {t.id: t for t in load_tasks()}
    for result_file in sorted(run_dir.glob("*/result.json")):
        old = read_json(result_file)
        if task_keys and old["task_key"] not in task_keys:
            continue
        task = tasks[old["task_id"]]
        workflow = load_workflow(old["workflow"]) if old.get("workflow") else None
        old.update(grade_task(task, Path(old["workspace"]), workflow))
        old["regraded"] = now_iso()
        write_json(result_file, old)
        print(f"  {old['task_key']}: score {old['score']}")
    return write_report(run_dir)
