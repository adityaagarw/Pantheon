"""Zeus's tools for the experience library and team tuning (experience.py, tuning.py)."""

from __future__ import annotations

import json

from app.services import experience, tuning
from app.tools.base import B, S, ToolContext, ToolError, obj
from app.tools.meta import meta


@meta("read_experience", "Read the experience library: lessons from tuning teams on real "
      "tasks, by kind of work. No arguments: the kinds of work. `domain`: its entries (cue, "
      "lesson, confidence). `domain` and `entry`: why that team was adopted, what each edit "
      "did, and the team.", obj({"domain": S, "entry": S}))
async def read_experience(args: dict, ctx: ToolContext) -> str:
    try:
        return experience.read(str(args.get("domain") or ""), str(args.get("entry") or ""))
    except experience.ExperienceError as e:
        raise ToolError(str(e)) from None


@meta("tune_team", "Tune an organization on a benchmark task (see the Benchmarks page for "
      "task ids, e.g. JobBench ones after an import): run it as it is, have two editors propose "
      "changes from what the run showed (one on how the team works, one on the field's "
      "know-how as skills), run both changed teams, adopt the changes the runs support, and "
      "store the lesson in the experience library. Takes about four runs of the task. The "
      "organization itself changes only with apply_tuning. Ask the user first: it costs four "
      "runs' tokens.", obj({"org": S, "task": S, "verify": {**B, "description":
                           "run the composed team once more to measure it (default true)"}},
                          ["org", "task"]))
async def tune_team(args: dict, ctx: ToolContext) -> str:
    try:
        t = await tuning.start(str(args["org"]), str(args["task"]),
                               verify=args.get("verify") is not False)
    except tuning.TuningError as e:
        raise ToolError(str(e)) from None
    except Exception as e:  # noqa: BLE001 - bench errors (no model server, no org)
        raise ToolError(str(e)) from None
    return (f"Started tuning {t.org_name} on {t.task_id} ({t.id}). It takes about four runs of "
            "the task; tuning_status shows how far it is.")


@meta("tuning_status", "How a team tuning is going, or the recent ones: each run's score, time "
      "and tokens, the proposed edits, which were adopted and why, and the lesson.",
      obj({"id": S}))
async def tuning_status(args: dict, ctx: ToolContext) -> str:
    if args.get("id"):
        t = tuning.load(str(args["id"]))
        if t is None:
            raise ToolError("no such tuning")
        return json.dumps(t.to_dict(), indent=1, ensure_ascii=False, default=str)
    rows = [f"- {t['id']} {t['org_name']} on {t['task_id']}: {t['status']} ({t['stage']}) "
            f"{t['detail']}" for t in tuning.history(10)]
    return "\n".join(rows) or "No tunings yet."


@meta("apply_tuning", "Change the organization to the team a tuning composed (its adopted "
      "edits: personas, tools, skills, the description). Say what will change and get the "
      "user's yes first.", obj({"id": S}, ["id"]))
async def apply_tuning(args: dict, ctx: ToolContext) -> str:
    try:
        changed = await tuning.apply(str(args["id"]))
    except tuning.TuningError as e:
        raise ToolError(str(e)) from None
    return "Applied: " + ("; ".join(changed) if changed else "nothing differed") + "."
