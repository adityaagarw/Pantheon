"""Load workflow templates and turn them into prompt text."""

import json
from dataclasses import dataclass
from pathlib import Path

from .tasks import BENCH_DIR, BenchError

WORKFLOWS_DIR = BENCH_DIR / "workflows"


@dataclass
class Workflow:
    name: str
    summary: str
    roles: list
    steps: list
    rules: list
    artifacts: list

    def to_markdown(self):
        lines = [f"## Workflow: {self.name}", "", self.summary, "", "### Roles", ""]
        for role in self.roles:
            lines.append(f"- **{role['name']}**: {role['job']}")
        lines += ["", "### Steps", ""]
        for number, step in enumerate(self.steps, 1):
            text = f"{number}. **{step['id']}** (role: {step['role']}). {step['do']}"
            if step.get("after"):
                text += f" Start after: {', '.join(step['after'])}."
            if step.get("output"):
                text += f" Output: `{step['output']}`."
            lines.append(text)
        if self.rules:
            lines += ["", "### Workflow rules", ""]
            lines += [f"- {rule}" for rule in self.rules]
        if self.artifacts:
            lines += [
                "",
                "### Required work records",
                "",
                "Write these files in the work folder. Each file must have real content "
                "(not a placeholder). The benchmark checks that they exist.",
                "",
            ]
            lines += [f"- `{path}`" for path in self.artifacts]
        return "\n".join(lines) + "\n"


def list_workflows():
    return sorted(p.stem for p in WORKFLOWS_DIR.glob("*.json"))


def load_workflow(name):
    path = WORKFLOWS_DIR / f"{name}.json"
    if not path.exists():
        raise BenchError(f"error: no workflow '{name}'. Known: {', '.join(list_workflows())}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return Workflow(
        name=data["name"],
        summary=data["summary"],
        roles=data["roles"],
        steps=data["steps"],
        rules=data.get("rules", []),
        artifacts=data.get("artifacts", []),
    )


def resolve_workflow(mode, task):
    """mode is 'task' (use the task's workflow), 'none', or a workflow name."""
    if mode == "none":
        return None
    if mode == "task":
        return load_workflow(task.workflow)
    return load_workflow(mode)
