"""Load benchmark tasks from the tasks/ folder."""

import json
from dataclasses import dataclass, field
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = BENCH_DIR / "tasks"

DEFAULT_WEIGHTS = {"hidden_tests": 70, "agent_tests": 10, "safety": 10, "process": 10}


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


def load_task(task_dir):
    task_dir = Path(task_dir)
    data = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(data.pop("weights", {}))
    return Task(path=task_dir, weights=weights, **data)


def load_tasks(selection="all", tasks_dir=TASKS_DIR):
    """Load tasks.

    selection is "all", or a comma list of task ids and "category:NAME" items.
    """
    tasks = [load_task(d) for d in sorted(Path(tasks_dir).iterdir()) if (d / "task.json").exists()]
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
