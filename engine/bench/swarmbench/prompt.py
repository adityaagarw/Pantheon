"""Build the prompt text that goes to the harness."""


def build_prompt(task, workflow, agent_workspace):
    body = task.prompt_path.read_text(encoding="utf-8").strip()
    has_tests = task.agent_tests_dir and (task.repo_path / task.agent_tests_dir).exists()
    if task.grading == "rubric":
        return _rubric_prompt(task, workflow, agent_workspace, body)

    lines = [
        f"# Task: {task.title}",
        "",
        f"Work folder: `{agent_workspace}`",
        "",
        body,
        "",
        "## General rules",
        "",
        "- Change files only in the work folder.",
        "- Use Python 3.10 and the Python standard library only. Do not install packages.",
        "- Do not ask questions. Nobody will answer. If something is not clear, make a "
        "sensible choice and write it in `worklog/NOTES.md`.",
        "- A hidden test suite will check your work after you stop.",
        "- When the work is complete, stop.",
    ]
    if task.agent_tests_dir:
        lines.append(f"- Put tests in `{task.agent_tests_dir}/`. Run them with: "
                     f"`python -m unittest discover -s {task.agent_tests_dir}` (from the work folder).")
    if task.protected_files:
        lines.append("- Do not change or delete these files: " + ", ".join(f"`{p}`" for p in task.protected_files) + ".")
    if has_tests:
        lines.append(f"- Do not change or delete the test files that are in `{task.agent_tests_dir}/` now. You can add new test files.")
    lines.append(f"- Time budget: about {task.time_limit_minutes} minutes.")
    lines.append("")
    if workflow is not None:
        lines.append(workflow.to_markdown())
    return "\n".join(lines).rstrip() + "\n"


def _rubric_prompt(task, workflow, agent_workspace, body):
    """Professional work (documents, workbooks) checked by a reviewer, not by tests."""
    out = task.output_dir or "the work folder"
    lines = [
        f"# Task: {task.title}",
        "",
        f"Work folder: `{agent_workspace}`. The task's files are in it.",
        "",
        body,
        "",
        "## General rules",
        "",
        f"- Write every deliverable to `{out}/` in the work folder, with the exact file names "
        "the task gives.",
        "- Change files only in the work folder, and don't change the files you were given.",
        "- Do not ask questions. Nobody will answer. If something is not clear, make a "
        "sensible choice and say so in the deliverable.",
        "- A reviewer checks your deliverables against the task after you stop.",
        "- When the work is complete, stop.",
        f"- Time budget: about {task.time_limit_minutes} minutes.",
        "",
    ]
    if workflow is not None:
        lines.append(workflow.to_markdown())
    return "\n".join(lines).rstrip() + "\n"
