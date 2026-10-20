"""Load benchmark tasks from the tasks/ folder (and imported ones, see external.py)."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = BENCH_DIR / "tasks"
# More task folders, e.g. benchmarks imported with `swarmbench import` (os.pathsep list).
EXTRA_ENV = "SWARMBENCH_TASK_DIRS"

DEFAULT_WEIGHTS = {"hidden_tests": 70, "agent_tests": 10, "safety": 10, "process": 10}
# A task graded by a rubric (documents and workbooks, not code): the rubric is the score.
RUBRIC_WEIGHTS = {"rubric": 90, "safety": 10, "hidden_tests": 0, "agent_tests": 0, "process": 0}


class BenchError(SystemExit):
    """A bad task, workflow or harness name. The CLI prints it; a library user can catch it."""


@dataclass
class Task:
    id: str
    title: str
    category: str
    difficulty: str
    workflow: str
    summary: str
    path: Path
    time_limit_minutes: int = 30
    grade_timeout_seconds: int = 180
    protected_files: list = field(default_factory=list)
    agent_tests_dir: str = "tests"
    # If a hidden test whose id contains one of these strings does not pass,
    # the hidden test score is 0. Use it when the start code already passes
    # most tests (for example a speed task, where correct but slow code is the start).
    gate_tests: list = field(default_factory=list)
    weights: dict = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    # "tests" (hidden unit tests) or "rubric" (a judge checks each criterion of rubric_file,
    # which stays outside the work folder).
    grading: str = "tests"
    rubric_file: str = "rubric/RUBRICS.json"
    # Folder in the work folder the deliverables go to (rubric tasks), "" for anywhere.
    output_dir: str = ""
    # Hosts the harness should block while the task runs: where its answers are public.
    blocked_hosts: list = field(default_factory=list)
    # Where an imported task came from, and its license.
    source: dict = field(default_factory=dict)

    @property
    def prompt_path(self):
        return self.path / "prompt.md"

    @property
    def repo_path(self):
        return self.path / "repo"

    @property
    def hidden_tests_path(self):
        return self.path / "hidden_tests"

    @property
    def solution_path(self):
        return self.path / "solution"

    @property
    def rubric_path(self):
        return self.path / self.rubric_file


def load_task(task_dir):
    task_dir = Path(task_dir)
    data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    weights = dict(RUBRIC_WEIGHTS if data.get("grading") == "rubric" else DEFAULT_WEIGHTS)
    weights.update(data.pop("weights", {}))
    known = Task.__dataclass_fields__
    return Task(path=task_dir, weights=weights, **{k: v for k, v in data.items() if k in known})


def task_dirs(tasks_dir=None):
    if tasks_dir is not None:
        return [Path(tasks_dir)]
    extra = [Path(p) for p in os.environ.get(EXTRA_ENV, "").split(os.pathsep) if p.strip()]
    return [TASKS_DIR, *extra]


def load_tasks(selection="all", tasks_dir=None):
    """Load tasks.

    selection is "all", or a comma list of task ids and "category:NAME" items.
    """
    tasks, seen = [], set()
    for root in task_dirs(tasks_dir):
        if not root.is_dir():
            continue
        for d in sorted(root.iterdir()):
            if (d / "task.json").exists():
                t = load_task(d)
                if t.id not in seen:
                    seen.add(t.id)
                    tasks.append(t)
    if not selection or selection == "all":
        return tasks
    wanted = [s.strip() for s in selection.split(",") if s.strip()]
    chosen = []
    for item in wanted:
        if item.startswith("category:"):
            matches = [t for t in tasks if t.category == item.split(":", 1)[1]]
        else:
            matches = [t for t in tasks if t.id == item]
        if not matches:
            raise BenchError(f"error: no task matches '{item}'. Use 'swarmbench list'.")
        chosen.extend(t for t in matches if t not in chosen)
    return chosen
