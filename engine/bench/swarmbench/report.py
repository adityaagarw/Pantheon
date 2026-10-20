"""Make Markdown reports from run results."""

from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev

from .utils import read_json, write_json


def load_results(run_dir):
    return [read_json(p) for p in sorted(Path(run_dir).glob("*/result.json"))]


def _part(result, name):
    part = result["parts"].get(name)
    if part is None or name not in result.get("weights", {}):
        return "-"
    return f"{part['score'] * 100:.0f}"


def _metric(results, key):
    values = [r.get("metrics", {}).get(key) for r in results]
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) if values else None


def _fmt(value, pattern="{:.0f}"):
    return "-" if value is None else pattern.format(value)


def render_run(run_dir):
    run_dir = Path(run_dir)
    info = read_json(run_dir / "run.json", {})
    results = load_results(run_dir)
    lines = [
        f"# SwarmBench report: {info.get('run_id', run_dir.name)}",
        "",
        f"- Harness: `{info.get('harness', {}).get('name', '?')}`",
        f"- Workflow mode: `{info.get('workflow_mode', '?')}`",
        f"- Started: {info.get('started', '?')}",
        f"- Task runs: {len(results)}",
        "",
    ]
    if not results:
        return "\n".join(lines + ["No results yet."]) + "\n"

    scores = [r["score"] for r in results]
    lines += [
        "## Summary",
        "",
        f"- **Mean score: {mean(scores):.1f} / 100**",
        f"- Tasks with full score: {sum(1 for s in scores if s >= 99.95)} of {len(scores)}",
        f"- Total wall time: {sum(r['run']['wall_seconds'] for r in results) / 60:.1f} min",
        f"- Timeouts: {sum(1 for r in results if r['run']['timed_out'])}",
        f"- Total tokens (if reported): {_fmt(_sum_tokens(results))}",
        f"- Total cost (if reported): {_fmt(_metric(results, 'cost_usd'), '${:.2f}')}",
        "",
        "## Results by task",
        "",
        "| Task | Category | Workflow | Score | Hidden tests | Agent tests | Safety | Process | Time (s) | Tokens | Cost |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in results:
        passed, total = checked(r)
        tokens = _sum_tokens([r])
        cost = r.get("metrics", {}).get("cost_usd")
        lines.append(
            f"| {r['task_key']} | {r['category']} | {r['workflow'] or 'none'} | {r['score']:.1f} | "
            f"{passed}/{total}{' (rubric)' if 'rubric' in r['parts'] else ''} | "
            f"{_part(r, 'agent_tests')} | {_part(r, 'safety')} | "
            f"{_part(r, 'process')} | {r['run']['wall_seconds']:.0f}{' (timeout)' if r['run']['timed_out'] else ''} | "
            f"{_fmt(tokens)} | {_fmt(cost, '${:.2f}')} |"
        )

    by_category = defaultdict(list)
    for r in results:
        by_category[r["category"]].append(r["score"])
    lines += ["", "## Mean score by category", "", "| Category | Runs | Mean score |", "| --- | ---: | ---: |"]
    for category, values in sorted(by_category.items()):
        lines.append(f"| {category} | {len(values)} | {mean(values):.1f} |")

    by_task = defaultdict(list)
    for r in results:
        by_task[r["task_id"]].append(r["score"])
    if any(len(v) > 1 for v in by_task.values()):
        lines += ["", "## Repeats (variance)", "", "| Task | Runs | Mean | Std dev | Min | Max |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
        for task_id, values in sorted(by_task.items()):
            lines.append(f"| {task_id} | {len(values)} | {mean(values):.1f} | {pstdev(values):.1f} | "
                         f"{min(values):.1f} | {max(values):.1f} |")

    failures = [(r["task_key"], r["parts"]["hidden_tests"]["failed"]) for r in results
                if (r["parts"].get("hidden_tests") or {}).get("failed")]
    if failures:
        lines += ["", "## Failed hidden tests", ""]
        for key, failed in failures:
            lines.append(f"- **{key}**: " + ", ".join(f"`{f.split('.')[-1]}`" for f in failed[:12])
                         + (f" and {len(failed) - 12} more" if len(failed) > 12 else ""))
    return "\n".join(lines) + "\n"


def checked(r):
    """(passed, total) of what graded the work: hidden tests, or a rubric's items."""
    parts = r["parts"]
    if parts.get("hidden_tests"):
        return parts["hidden_tests"]["passed"], parts["hidden_tests"]["total"]
    rubric = parts.get("rubric") or {}
    return rubric.get("items_met", 0), len(rubric.get("items") or [])


def _sum_tokens(results):
    total = 0
    found = False
    for r in results:
        metrics = r.get("metrics", {})
        for key in ("input_tokens", "output_tokens"):
            if isinstance(metrics.get(key), (int, float)):
                total += metrics[key]
                found = True
    return total if found else None


def summarize(run_dir):
    """The run in numbers, for tools and dashboards (also written as summary.json)."""
    run_dir = Path(run_dir)
    info = read_json(run_dir / "run.json", {})
    results = load_results(run_dir)
    scores = [r["score"] for r in results]
    cost = _metric(results, "cost_usd")
    tokens = _sum_tokens(results)
    wall = sum(r["run"]["wall_seconds"] for r in results)
    return {
        "run_id": info.get("run_id", run_dir.name),
        "harness": info.get("harness", {}).get("name"),
        "workflow_mode": info.get("workflow_mode"),
        "started": info.get("started"),
        "runs": len(results),
        "mean_score": round(mean(scores), 1) if scores else None,
        "full_scores": sum(1 for s in scores if s >= 99.95),
        "timeouts": sum(1 for r in results if r["run"]["timed_out"]),
        "wall_seconds": round(wall, 1),
        "tokens": tokens,
        "cost_usd": cost,
        "points_per_dollar": round(sum(scores) / cost, 1) if cost else None,
        "tasks": [{
            "key": r["task_key"], "task_id": r["task_id"], "category": r["category"],
            "workflow": r["workflow"], "score": r["score"],
            "hidden_passed": checked(r)[0],
            "hidden_total": checked(r)[1],
            "parts": {k: (v["score"] if v else None) for k, v in r["parts"].items()},
            "wall_seconds": r["run"]["wall_seconds"], "timed_out": r["run"]["timed_out"],
            "tokens": _sum_tokens([r]), "cost_usd": r.get("metrics", {}).get("cost_usd"),
        } for r in results],
    }


def write_report(run_dir):
    path = Path(run_dir) / "report.md"
    path.write_text(render_run(run_dir), encoding="utf-8")
    write_json(Path(run_dir) / "summary.json", summarize(run_dir))
    return path


def render_compare(run_dirs):
    runs = []
    for run_dir in run_dirs:
        info = read_json(Path(run_dir) / "run.json", {})
        label = f"{info.get('harness', {}).get('name', Path(run_dir).name)} ({info.get('workflow_mode', '?')})"
        by_task = defaultdict(list)
        for r in load_results(run_dir):
            by_task[r["task_id"]].append(r)
        runs.append((Path(run_dir).name, label, by_task))

    task_ids = sorted({t for _, _, by_task in runs for t in by_task})
    lines = ["# SwarmBench comparison", ""]
    lines += [f"- **R{i}**: `{name}` - {label}" for i, (name, label, _) in enumerate(runs, 1)]
    lines += ["", "## Mean score per task", ""]
    header = "| Task | " + " | ".join(f"R{i}" for i in range(1, len(runs) + 1)) + " |"
    lines += [header, "| --- |" + " ---: |" * len(runs)]
    for task_id in task_ids:
        cells = []
        for _, _, by_task in runs:
            values = [r["score"] for r in by_task.get(task_id, [])]
            cells.append(f"{mean(values):.1f}" if values else "-")
        lines.append(f"| {task_id} | " + " | ".join(cells) + " |")

    lines += ["", "## Totals", "",
              "| Run | Mean score | Full scores | Wall time (min) | Tokens | Cost | Points per $ | Tokens per point |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for i, (_, _, by_task) in enumerate(runs, 1):
        flat = [r for rs in by_task.values() for r in rs]
        score = mean(r["score"] for r in flat) if flat else 0
        points = sum(r["score"] for r in flat)
        wall = sum(r["run"]["wall_seconds"] for r in flat) / 60
        cost = _metric(flat, "cost_usd")
        tokens = _sum_tokens(flat)
        lines.append(f"| R{i} | {score:.1f} | {sum(1 for r in flat if r['score'] >= 99.95)}/{len(flat)} | "
                     f"{wall:.1f} | {_fmt(tokens)} | {_fmt(cost, '${:.2f}')} | "
                     f"{_fmt(points / cost if cost else None, '{:.0f}')} | "
                     f"{_fmt(tokens / points if tokens and points else None, '{:.0f}')} |")
    return "\n".join(lines) + "\n"
