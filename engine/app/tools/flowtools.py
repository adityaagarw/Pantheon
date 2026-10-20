"""Flows (services/flows.py): finishing a step (whoever is doing one has finish_step), and
Zeus making and running flows."""

from __future__ import annotations

from app.services import flows
from app.tools.base import B, S, ToolContext, ToolError, arr, obj, tool
from app.tools.meta import _org, meta


@tool("finish_step", "Finish the flow step you're doing, with its result: what the next "
      "steps get (complete on its own: they see nothing else of your work). If you can't "
      "do it, failed=true with why; the steps after it won't run. `step`: the step id from "
      "its message, when you're doing more than one.",
      obj({"output": S, "failed": B, "step": S}, ["output"]), category="flow")
async def finish_step(args: dict, ctx: ToolContext) -> str:
    found = await _step(ctx, str(args["step"])) if args.get("step") else \
        await flows.running_step_for(ctx.agent.id, ctx.org.id)
    if found is None:
        raise ToolError("you're not doing a flow step right now")
    run, node_id = found
    output = str(args["output"]).strip()
    if not output:
        raise ToolError("give the step's result (or why it failed)")
    try:
        run = await flows.finish_step(run.id, node_id, output, failed=bool(args.get("failed")))
    except flows.FlowError as e:
        raise ToolError(str(e)) from None
    nxt = [n["title"] for n in run.graph["nodes"]
           if (run.steps or {}).get(n["id"], {}).get("status") == "running"]
    if run.status != "running":
        return f"Step finished. The run is {run.status}."
    return "Step finished." + (f" Now running: {', '.join(nxt)}." if nxt else "")


async def _step(ctx: ToolContext, node_id: str):
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import FlowRun

    async with SessionLocal() as session:
        rows = list((await session.execute(select(FlowRun).where(
            FlowRun.org_id == ctx.org.id, FlowRun.status == "running"))).scalars())
    for run in rows:
        node = next((n for n in run.graph.get("nodes") or [] if n["id"] == node_id), None)
        if node and node["agentId"] == ctx.agent.id and \
                (run.steps or {}).get(node_id, {}).get("status") == "running":
            return run, node_id
    return None


STEP = {"type": "object", "required": ["id", "agent"], "properties": {
    "id": {"type": "string", "description": "short id, e.g. research"},
    "title": S, "agent": {"type": "string", "description": "the agent's name"},
    "instructions": {"type": "string", "description": "what this step does and what its "
                     "result should be"},
    "files": {"type": "array", "items": {"type": "string"}, "description": "the files this "
              "step makes (names or patterns, e.g. schedule.html): its agent's alone while it "
              "runs, and left as they are while later steps build on them"}}}
EDGE = {"type": "object", "required": ["from", "to"],
        "properties": {"from": S, "to": S}}


# --- a lead: a flow made for the goal at hand ---------------------------------------------
# A team that coordinates by tasks and messages lost most of its time to talk, reviews and
# waiting (a timed run: 70 minutes and 126 messages where one agent took 10). A flow has no
# chatter: each step gets the brief and what the steps before it made, does one job and
# finishes. plan_flow lets a lead shape that graph to the goal in hand, then run it.


@tool("plan_flow", "Run the goal as a flow you design for it: steps (each one of your "
      "people, or you, doing one job) and edges (a step starts when the steps before it are "
      "done and gets their results). Steps without edges between them run at the same time. "
      "Every step gets the goal's request and the results of the steps before it, and "
      "nothing else, so write each step's instructions to stand alone: what to make, from "
      "what, and what its result must say. Give every step that makes files its `files`, "
      "and no two steps running at once the same file: nobody else can change a step's "
      "files while it runs. Nobody messages anybody: you are told when the "
      "run ends, with the last steps' results; then hand in the goal. For a fix after the "
      "acceptance check, plan a small flow of just the fixes.",
      obj({"name": S, "steps": arr(STEP), "edges": arr(EDGE),
           "goal": {"type": "string", "description": "the goal's task ref (default: your open "
                    "goal)"}}, ["name", "steps"]),
      category="lead")
async def plan_flow(args: dict, ctx: ToolContext) -> str:
    from app.services import tasks

    goal = await _goal(ctx, str(args.get("goal") or ""))
    steps = list(args["steps"])
    edges = list(args.get("edges") or [])
    try:
        f = await flows.create(ctx.org.id, args["name"],
                               {"nodes": flows.layout(steps, edges), "edges": edges},
                               f"Planned by {ctx.agent.name} for {tasks.task_ref(goal)}.")
        run = await flows.start(f.id, goal.description or goal.title,
                                origin={"leadId": ctx.agent.id, "goalId": goal.id})
    except flows.FlowError as e:
        raise ToolError(str(e)) from None
    if goal.status in ("todo", "backlog"):
        await tasks.update_task(ctx.org.id, ctx.sender, tasks.task_ref(goal), status="in_progress")
    now = [n["title"] for n in run.graph["nodes"]
           if (run.steps or {}).get(n["id"], {}).get("status") == "running"]
    return (f"Started '{f.name}' (run {run.number}, {len(steps)} steps) for "
            f"{tasks.task_ref(goal)}. Running now: {', '.join(now)}. You'll get a message "
            "when it ends; until then there's nothing to do for it, so end your turn.")


async def _goal(ctx: ToolContext, ref: str):
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Task
    from app.services import tasks

    async with SessionLocal() as session:
        if ref:
            t = await tasks.find_task(session, ctx.org.id, ref)
            if t is None:
                raise ToolError(f"no task {ref}")
            return t
        t = (await session.execute(select(Task).where(
            Task.org_id == ctx.org.id, Task.assignee_id == ctx.agent.id,
            Task.reporter_id == "user", Task.parent_id.is_(None),
            Task.status.not_in(tasks.CLOSED)).order_by(Task.created_at.desc()))).scalars().first()
    if t is None:
        raise ToolError("no open goal of yours: record the user's request first "
                        "(create_task with goal=true), or name the task with `goal`")
    return t


# --- Zeus: designing and running flows ----------------------------------------------------


@meta("create_flow", "Make a flow for an organization: steps as a directed acyclic graph, "
      "each step one agent doing one thing, edges handing a step's result to the next. Use "
      "flows for work with a clear sequence (research → draft → review); they keep agents "
      "from talking over each other. Set the organization's mode to flow (update_org "
      "settings.mode) when all its work should run this way.",
      obj({"org": S, "name": S, "steps": arr(STEP), "edges": arr(EDGE), "description": S},
          ["org", "name", "steps"]))
async def create_flow(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    steps = list(args["steps"])
    edges = list(args.get("edges") or [])
    try:
        f = await flows.create(org.id, args["name"], {"nodes": flows.layout(steps, edges), "edges": edges},
                               args.get("description") or "")
    except flows.FlowError as e:
        raise ToolError(str(e)) from None
    return (f"Made the flow '{f.name}' ({f.id}) in {org.name} with {len(steps)} steps. "
            "The user sees it in the Flows pane; run it with run_flow.")


@meta("run_flow", "Run one of an organization's flows with an input (a question, a brief).",
      obj({"org": S, "flow": {"type": "string", "description": "the flow's name or id"},
           "input": S}, ["org", "flow"]))
async def run_flow(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    ref = str(args["flow"]).strip().lower()
    match = next((f for f in await flows.for_org(org.id)
                  if f.id == args["flow"] or f.name.lower() == ref), None)
    if match is None:
        raise ToolError(f"no flow '{args['flow']}' in {org.name}")
    try:
        run = await flows.start(match.id, str(args.get("input") or ""))
    except flows.FlowError as e:
        raise ToolError(str(e)) from None
    return f"Started run {run.number} of '{match.name}'. The user can follow it in the Flows pane."
