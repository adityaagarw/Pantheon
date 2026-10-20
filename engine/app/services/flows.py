"""Flows: work as a directed acyclic graph of steps, with no free-form chatter.

A flow's steps each name an agent and what to do. An edge hands one step's output to the
next. Running a flow starts every step whose inputs are ready: its agent gets one message
with the run's input and the outputs of the steps before it, does that step alone, and
finishes it with finish_step (or by ending its turn: its final reply is the output). A step
starts as soon as all the steps feeding it are done; a failed step skips everything after
it. The run is done when every step is.

A step can name the files it makes (`files`, names or patterns). While it runs they are
its agent's alone, and once it is done they stay as they are while other people's steps
build on them; the steps after those may change them. A timed run without this lost most
of its hour to three builders working the same files: one generated every screen from his
own parts and overwrote the other two, who spent the rest reconciling.

Runs keep the graph as it was when they started, so editing a flow never changes a run.
"""

from __future__ import annotations

import asyncio
import fnmatch
import glob
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.events import bus
from app.models import Agent, Flow, FlowRun, Org, Turn

MAX_STEPS = 60
MAX_OUTPUT = 20_000  # characters of a step's output passed downstream
FINAL = ("done", "failed", "skipped")


class FlowError(ValueError):
    pass


# --- serialization ----------------------------------------------------------------------------


def flow_to_dict(f: Flow) -> dict[str, Any]:
    return {"id": f.id, "orgId": f.org_id, "name": f.name, "description": f.description,
            "graph": f.graph or {"nodes": [], "edges": []},
            "createdAt": f.created_at.isoformat() if f.created_at else None,
            "updatedAt": f.updated_at.isoformat() if f.updated_at else None}


def run_to_dict(r: FlowRun) -> dict[str, Any]:
    return {"id": r.id, "flowId": r.flow_id, "orgId": r.org_id, "number": r.number,
            "input": r.input, "status": r.status, "graph": r.graph, "steps": r.steps or {},
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "finishedAt": r.finished_at.isoformat() if r.finished_at else None}


# --- the graph --------------------------------------------------------------------------------


def upstream(graph: dict[str, Any]) -> dict[str, list[str]]:
    ups: dict[str, list[str]] = defaultdict(list)
    for e in graph.get("edges") or []:
        ups[e["to"]].append(e["from"])
    return ups


async def validate(org_id: str, graph: dict[str, Any]) -> dict[str, Any]:
    """A clean graph, or FlowError saying what's wrong: every step needs an agent of this
    organization and something to do; edges join steps; nothing may loop back."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise FlowError("the graph needs nodes and edges lists")
    if len(nodes) > MAX_STEPS:
        raise FlowError(f"a flow has at most {MAX_STEPS} steps")
    async with SessionLocal() as session:
        agents = {a.id: a for a in (await session.execute(select(Agent).where(
            Agent.org_id == org_id, Agent.is_supervisor.is_(False)))).scalars()}
    by_name = {a.name.lower(): a.id for a in agents.values()}
    clean_nodes: list[dict[str, Any]] = []
    ids: set[str] = set()
    for i, n in enumerate(nodes):
        nid = str(n.get("id") or f"step{i + 1}")
        if nid in ids:
            raise FlowError(f"two steps have the id '{nid}'")
        ids.add(nid)
        agent = str(n.get("agentId") or n.get("agent") or "")
        agent = agent if agent in agents else by_name.get(agent.lower(), "")
        title = str(n.get("title") or "").strip() or f"Step {i + 1}"
        if not agent:
            raise FlowError(f"step '{title}' needs one of this organization's agents")
        files = n.get("files") or []
        if isinstance(files, str):
            files = [f for f in re.split(r"[,\n]", files)]
        files = [str(f).strip().replace("\\", "/").removeprefix("./") for f in files][:40]
        clean_nodes.append({"id": nid, "title": title[:120], "agentId": agent,
                            "instructions": str(n.get("instructions") or "")[:8000],
                            "x": float(n.get("x") or 0), "y": float(n.get("y") or 0),
                            **({"files": [f[:200] for f in files if f]} if any(files) else {})})
    clean_edges: list[dict[str, str]] = []
    for e in edges:
        a, b = str(e.get("from") or ""), str(e.get("to") or "")
        if a not in ids or b not in ids or a == b:
            raise FlowError(f"an edge joins steps that aren't in the flow ({a} → {b})")
        if {"from": a, "to": b} not in clean_edges:
            clean_edges.append({"from": a, "to": b})
    # Kahn's algorithm: whatever never becomes ready is part of a loop.
    ins = {nid: 0 for nid in ids}
    outs: dict[str, list[str]] = defaultdict(list)
    for e in clean_edges:
        ins[e["to"]] += 1
        outs[e["from"]].append(e["to"])
    ready = [n for n, k in ins.items() if k == 0]
    seen = 0
    while ready:
        n = ready.pop()
        seen += 1
        for m in outs[n]:
            ins[m] -= 1
            if ins[m] == 0:
                ready.append(m)
    if seen != len(ids):
        looped = [n["title"] for n in clean_nodes if ins[n["id"]] > 0]
        raise FlowError(f"the steps loop back on themselves ({', '.join(looped)}): a flow "
                        "only goes forward")
    return {"nodes": clean_nodes, "edges": clean_edges}


def layout(steps: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Place steps left to right by how many steps come before them."""
    level = {s["id"]: 0 for s in steps}
    for _ in steps:
        for e in edges:
            if e["from"] in level and e["to"] in level:
                level[e["to"]] = max(level[e["to"]], level[e["from"]] + 1)
    rows: dict[int, int] = {}
    out = []
    for s in steps:
        lv = level[s["id"]]
        out.append({**s, "x": lv * 320, "y": rows.get(lv, 0) * 190})
        rows[lv] = rows.get(lv, 0) + 1
    return out


# --- flows ------------------------------------------------------------------------------------


async def create(org_id: str, name: str, graph: dict[str, Any], description: str = "") -> Flow:
    name = name.strip()
    if not name:
        raise FlowError("a flow needs a name")
    clean = await validate(org_id, graph)
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise FlowError("organization not found")
        f = Flow(org_id=org_id, name=name[:120], description=description, graph=clean)
        session.add(f)
        await session.commit()
    await bus.publish("flow.updated", flow_to_dict(f), org_id=org_id)
    return f


async def update(flow_id: str, patch: dict[str, Any]) -> Flow:
    async with SessionLocal() as session:
        f = await session.get(Flow, flow_id)
        if f is None:
            raise FlowError("flow not found")
        org_id = f.org_id
    clean = await validate(org_id, patch["graph"]) if "graph" in patch else None
    async with SessionLocal() as session:
        f = await session.get(Flow, flow_id)
        assert f is not None
        if patch.get("name"):
            f.name = str(patch["name"]).strip()[:120]
        if "description" in patch:
            f.description = str(patch["description"] or "")
        if clean is not None:
            f.graph = clean
        await session.commit()
    await bus.publish("flow.updated", flow_to_dict(f), org_id=org_id)
    return f


async def delete(flow_id: str) -> None:
    async with SessionLocal() as session:
        f = await session.get(Flow, flow_id)
        if f is None:
            raise FlowError("flow not found")
        org_id = f.org_id
        await session.delete(f)
        await session.commit()
    await bus.publish("flow.deleted", {"id": flow_id}, org_id=org_id)


async def for_org(org_id: str) -> list[Flow]:
    async with SessionLocal() as session:
        return list((await session.execute(select(Flow).where(Flow.org_id == org_id)
                                           .order_by(Flow.created_at))).scalars())


async def runs(flow_id: str, limit: int = 30) -> list[FlowRun]:
    async with SessionLocal() as session:
        return list((await session.execute(select(FlowRun).where(FlowRun.flow_id == flow_id)
                                           .order_by(FlowRun.created_at.desc()).limit(limit))).scalars())


# --- running ----------------------------------------------------------------------------------

_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def flow_for_chat(org_id: str, to_agents: list[str]) -> Flow | None:
    """In a flow-mode organization what the user says starts a run of its flow (the one run
    last, else the first), unless they're talking to an agent in the middle of a step: that's
    an answer to it."""
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None or (org.settings or {}).get("mode") != "flow":
            return None
        last = await session.scalar(select(FlowRun.flow_id).where(FlowRun.org_id == org_id)
                                    .order_by(FlowRun.created_at.desc()).limit(1))
    for agent_id in to_agents:
        if await running_step_for(agent_id, org_id) is not None:
            return None
    usable = [f for f in await for_org(org_id) if (f.graph or {}).get("nodes")]
    return next((f for f in usable if f.id == last), usable[0] if usable else None)


def in_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """The steps in the order they run (by how many steps come before each)."""
    placed = {s["id"]: s for s in layout(graph.get("nodes") or [], graph.get("edges") or [])}
    return sorted((n for n in graph.get("nodes") or []),
                  key=lambda n: (placed[n["id"]]["x"], placed[n["id"]]["y"]))


async def start(flow_id: str, input_: str, origin: dict[str, Any] | None = None) -> FlowRun:
    async with SessionLocal() as session:
        f = await session.get(Flow, flow_id)
        if f is None:
            raise FlowError("flow not found")
        graph = f.graph or {}
        if not graph.get("nodes"):
            raise FlowError("the flow has no steps yet")
        n = (await session.scalar(select(func.count()).select_from(FlowRun).where(
            FlowRun.flow_id == flow_id)) or 0) + 1
        run = FlowRun(id=new_id("run"), flow_id=flow_id, org_id=f.org_id, number=n,
                      input=input_.strip(), graph=graph, origin=origin,
                      steps={node["id"]: {"status": "pending"} for node in graph["nodes"]})
        session.add(run)
        await session.commit()
    await bus.publish("flowrun.updated", run_to_dict(run), org_id=run.org_id)
    await advance(run.id)
    return run


async def advance(run_id: str) -> FlowRun:
    """Start every step whose inputs are all done; skip those after a failure; finish the
    run when nothing is left to do."""
    ended = False
    async with _locks[run_id]:
        async with SessionLocal() as session:
            run = await session.get(FlowRun, run_id)
            if run is None:
                raise FlowError("run not found")
            if run.status != "running":
                return run
            steps = {k: dict(v) for k, v in (run.steps or {}).items()}
            ups = upstream(run.graph)
            to_start: list[dict[str, Any]] = []
            changed = True
            while changed:  # skips cascade down the graph
                changed = False
                for node in run.graph["nodes"]:
                    s = steps[node["id"]]
                    if s["status"] != "pending":
                        continue
                    before = [steps[u]["status"] for u in ups[node["id"]]]
                    if any(b in ("failed", "skipped") for b in before):
                        s.update(status="skipped", finishedAt=utcnow().isoformat())
                        changed = True
                    elif all(b == "done" for b in before):
                        s.update(status="running", startedAt=utcnow().isoformat())
                        to_start.append(node)
            if all(s["status"] in FINAL for s in steps.values()):
                run.status = "done" if all(s["status"] == "done" for s in steps.values()) \
                    else "failed"
                run.finished_at = utcnow()
                ended = True
            run.steps = steps
            await session.commit()
        for node in to_start:
            await _send_step(run, node, steps)
    await bus.publish("flowrun.updated", run_to_dict(run), org_id=run.org_id)
    if ended:
        await _answer(run)
    return run


async def _answer(run: FlowRun) -> None:
    """A run started from a conversation answers there: the last steps' results, or which
    step failed and why. It lands in the user's Inbox too, and wakes nobody."""
    from app.models import Channel, UserInboxItem
    from app.services import comms

    origin = run.origin or {}
    if origin.get("leadId"):
        await _tell_lead(run)
        return
    if not origin.get("channelId"):
        return
    nodes = {n["id"]: n for n in run.graph["nodes"]}
    feeds = {e["from"] for e in run.graph.get("edges") or []}
    steps = run.steps or {}
    async with SessionLocal() as session:
        ch = await session.get(Channel, origin["channelId"])
        f = await session.get(Flow, run.flow_id)
        names = {a.id: a.name for a in (await session.execute(select(Agent).where(
            Agent.org_id == run.org_id))).scalars()}
    if ch is None:
        return
    flow_name = f.name if f else "the flow"
    sender = comms.Sender.system()
    if run.status == "done":
        ends = [nid for nid in nodes if nid not in feeds]
        if len(ends) == 1:
            sender = comms.Sender("agent", nodes[ends[0]]["agentId"])
            content = str(steps[ends[0]].get("output") or "")
        else:
            content = "\n\n".join(f"## {nodes[n]['title']} ({names.get(nodes[n]['agentId'], '?')})"
                                  f"\n{steps[n].get('output') or ''}" for n in ends)
    else:
        bad = next((n for n in nodes if steps.get(n, {}).get("status") == "failed"), None)
        why = f": {nodes[bad]['title']} ({names.get(nodes[bad]['agentId'], '?')}) couldn't " \
              f"finish it. {steps[bad].get('error') or ''}" if bad else "."
        content = f"Run {run.number} of the flow '{flow_name}' stopped{why}"
    msg = await comms.post(run.org_id, sender, content, channel=ch, kind="reply",
                           reply_to=origin.get("messageId"), deliver=False,
                           meta={"flowRun": {"runId": run.id, "flowId": run.flow_id,
                                             "number": run.number, "status": run.status}})
    async with SessionLocal() as session:
        session.add(UserInboxItem(org_id=run.org_id, kind="message", ref_id=msg.id,
                                  title=f"{flow_name}, run {run.number}: {content[:160]}"))
        await session.commit()


async def _tell_lead(run: FlowRun) -> None:
    """A run a lead planned for a goal (plan_flow) ends with a message to that lead: the last
    steps' results, or the step that failed, so they hand in the goal or plan the fix."""
    from app.services import comms, tasks

    origin = run.origin or {}
    nodes = {n["id"]: n for n in run.graph["nodes"]}
    feeds = {e["from"] for e in run.graph.get("edges") or []}
    steps = run.steps or {}
    async with SessionLocal() as session:
        names = {a.id: a.name for a in (await session.execute(select(Agent).where(
            Agent.org_id == run.org_id))).scalars()}
        goal = await session.get(tasks.Task, origin.get("goalId") or "")
    ref = tasks.task_ref(goal) if goal else "the goal"
    if run.status == "done":
        ends = [nid for nid in nodes if nid not in feeds]
        body = "\n\n".join(f"## {nodes[n]['title']} ({names.get(nodes[n]['agentId'], '?')})\n"
                           f"{str(steps[n].get('output') or '')[:MAX_OUTPUT]}" for n in ends)
        text = (f"Your flow for {ref} finished (run {run.number}). The last steps' results:\n\n"
                f"{body}\n\nIf the joined result works, hand in {ref} (move it to review with "
                "a short result); its acceptance check tests the whole thing.")
    else:
        bad = [n for n in nodes if steps.get(n, {}).get("status") == "failed"]
        why = "; ".join(f"{nodes[n]['title']} ({names.get(nodes[n]['agentId'], '?')}): "
                        f"{steps[n].get('error') or 'no reason given'}" for n in bad)
        text = (f"Your flow for {ref} stopped (run {run.number}, {run.status})"
                + (f". Failed: {why}" if why else ".") + " Plan a flow for what's left, or do it.")
    await comms.notify(run.org_id, [origin["leadId"]], text, task_id=goal.id if goal else None)


def _step_message(run: FlowRun, flow_name: str, node: dict[str, Any],
                  steps: dict[str, Any], names: dict[str, str]) -> str:
    ups = upstream(run.graph)[node["id"]]
    titles = {n["id"]: n for n in run.graph["nodes"]}
    parts = [f"# Flow step: {node['title']}",
             f'(flow "{flow_name}", run {run.number}; step id {node["id"]})']
    if node.get("instructions"):
        parts += ["", node["instructions"]]
    others = [f"{', '.join(n['files'])} ({names.get(n['agentId'], '?')})"
              for n in run.graph["nodes"] if n["id"] != node["id"] and n.get("files")]
    if node.get("files"):
        parts += ["", f"Your files: {', '.join(node['files'])}. Make only these."
                  + (f" The others belong to their steps: {'; '.join(others)}." if others else "")]
    elif len(run.graph["nodes"]) > 1:
        parts += ["", "The files you write first in this step are yours until it ends; "
                  "files other steps made are theirs, so build in your own."
                  + (f" Named already: {'; '.join(others)}." if others else "")]
    if run.input:
        parts += ["", "## The run's input", run.input]
    if ups:
        parts += ["", "## From the steps before yours"]
        for u in ups:
            out = str(steps[u].get("output") or "")
            if len(out) > MAX_OUTPUT:
                out = out[:MAX_OUTPUT] + "\n… (cut)"
            parts += ["", f"### {titles[u]['title']} ({names.get(titles[u]['agentId'], '?')})", out]
    parts += ["", "Do this step only. When it's done, call finish_step with your result: "
              "it's what the next steps get, so make it complete on its own. If you can't do "
              "it, call finish_step with failed=true and say why."]
    return "\n".join(parts)


async def _send_step(run: FlowRun, node: dict[str, Any], steps: dict[str, Any]) -> None:
    from app.services import comms

    async with SessionLocal() as session:
        f = await session.get(Flow, run.flow_id)
        names = {a.id: a.name for a in (await session.execute(select(Agent).where(
            Agent.org_id == run.org_id))).scalars()}
    content = _step_message(run, f.name if f else "flow", node, steps, names)
    meta: dict[str, Any] = {"flow": {"runId": run.id, "nodeId": node["id"], "flowId": run.flow_id}}
    files = (run.origin or {}).get("attachments")
    if files and not upstream(run.graph)[node["id"]]:
        meta["attachments"] = files  # what the user attached goes to the first steps
    msg = await comms.post(run.org_id, comms.Sender.system(), content,
                           to_agents=[node["agentId"]], kind="flow_step", meta=meta)
    async with SessionLocal() as session:
        row = await session.get(FlowRun, run.id)
        if row is not None:
            s = dict(row.steps or {})
            s[node["id"]] = {**s.get(node["id"], {}), "messageId": msg.id}
            row.steps = s
            await session.commit()


_FILE_NAME = re.compile(r"\b[\w./-]+\.(?:html?|css|jsx?|tsx?|mjs|cjs|md|py|json|ya?ml|svg|"
                        r"csv|txt|sql|go|rs|java|kt|swift|rb|php|c|h|cpp|cs)\b", re.I)


async def finish_step(run_id: str, node_id: str, output: str, *, failed: bool = False) -> FlowRun:
    async with _locks[run_id]:
        async with SessionLocal() as session:
            run = await session.get(FlowRun, run_id)
            if run is None:
                raise FlowError("run not found")
            steps = {k: dict(v) for k, v in (run.steps or {}).items()}
            s = steps.get(node_id)
            if s is None:
                raise FlowError(f"no step {node_id} in this run")
            if s["status"] != "running":
                raise FlowError(f"that step is {s['status']}, not running")
            # Pantheon's own record of what the step changed goes with its result: a builder's
            # result listed fixes to colleagues' pages it had never touched.
            changed = s.get("changed") or []
            if changed and not failed:
                output = (output.rstrip() + "\n\nFiles this step changed (Pantheon's record): "
                          + ", ".join(changed[:60])
                          + (f" and {len(changed) - 60} more" if len(changed) > 60 else ""))
            elif not failed and _FILE_NAME.search(output):
                output = output.rstrip() + "\n\nPantheon recorded no file changes in this step."
            s.update(status="failed" if failed else "done", finishedAt=utcnow().isoformat(),
                     **({"error": output} if failed else {"output": output}))
            run.steps = steps
            await session.commit()
    return await advance(run_id)


async def running_step_for(agent_id: str, org_id: str) -> tuple[FlowRun, str] | None:
    """The step this agent is doing now (the oldest, if several)."""
    async with SessionLocal() as session:
        rows = list((await session.execute(select(FlowRun).where(
            FlowRun.org_id == org_id, FlowRun.status == "running")
            .order_by(FlowRun.created_at))).scalars())
    for run in rows:
        for node in run.graph.get("nodes") or []:
            if node["agentId"] == agent_id and (run.steps or {}).get(node["id"], {}).get(
                    "status") == "running":
                return run, node["id"]
    return None


def claims(rel: str, patterns: list[str]) -> bool:
    """Whether a workspace path (relative, with /) is one of a step's files."""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(rel, p) or ("/" not in p and fnmatch.fnmatch(name, p))
               for p in patterns)


async def fences(org_id: str, agent_id: str) -> tuple[list[str], list[tuple[list[str], str]]]:
    """For an agent now: the files of its own running steps, and the files it must leave
    alone, each with why: another person's running step makes them, or they came from a
    finished step that someone else's running step builds on."""
    async with SessionLocal() as session:
        runs = list((await session.execute(select(FlowRun).where(
            FlowRun.org_id == org_id, FlowRun.status == "running"))).scalars())
        if not runs:
            return [], []
        names = {a.id: a.name for a in (await session.execute(select(Agent).where(
            Agent.org_id == org_id))).scalars()}
    own: list[str] = []
    fenced: list[tuple[list[str], str]] = []
    for run in runs:
        nodes = {n["id"]: n for n in run.graph.get("nodes") or []}
        steps = run.steps or {}
        after: dict[str, list[str]] = defaultdict(list)
        for e in run.graph.get("edges") or []:
            after[e["from"]].append(e["to"])

        def status(nid: str, steps: dict = steps) -> str:
            return str(steps.get(nid, {}).get("status") or "")

        for nid, n in nodes.items():
            files = [*(n.get("files") or []), *steps.get(nid, {}).get("made", [])]
            if not files:
                continue
            if n["agentId"] == agent_id and status(nid) == "running":
                own += files
            elif status(nid) == "running":
                fenced.append((files, f"{names.get(n['agentId'], 'someone')} is making it in "
                                      f"the step '{n['title']}' right now"))
            elif status(nid) == "done":
                users = [c for c in after[nid] if status(c) == "running"
                         and nodes[c]["agentId"] != agent_id]
                if users:
                    who = sorted({names.get(nodes[c]["agentId"], "someone") for c in users})
                    fenced.append((files, f"it came from the step '{n['title']}', and "
                                          f"{' and '.join(who)} build on it right now"))
    return own, fenced


async def note_made(org_id: str, agent_id: str, rel: str | list[str], *,
                    claim: bool = True) -> None:
    """Files an agent doing a step changed: recorded as the step's changes, and (`claim`)
    a file written first becomes the step's, unless it's already someone's: whoever writes
    a file first in a run makes it. A command's changes are recorded without claiming."""
    hit = await running_step_for(agent_id, org_id)
    if hit is None:
        return
    run_id, nid = hit[0].id, hit[1]
    rels = [rel] if isinstance(rel, str) else rel
    async with _locks[run_id]:
        async with SessionLocal() as session:
            run = await session.get(FlowRun, run_id)
            if run is None or run.status != "running":
                return
            steps = {k: dict(v) for k, v in (run.steps or {}).items()}
            node = next((n for n in run.graph.get("nodes") or [] if n["id"] == nid), {})
            s = steps.get(nid) or {}
            if s.get("status") != "running":
                return
            changed = list(s.get("changed") or [])
            for r in rels:
                if r not in changed:
                    changed = [*changed, r][-200:]
                if not claim or claims(r, [*(node.get("files") or []), *s.get("made", [])]):
                    continue
                if any(claims(r, [*(n.get("files") or []), *steps.get(n["id"], {}).get("made", [])])
                       for n in run.graph.get("nodes") or [] if n["id"] != nid):
                    continue  # another step's file (this agent may change it now: not claimed)
                s["made"] = [*s.get("made", []), glob.escape(r)][-200:]  # a name, not a pattern
            if changed == s.get("changed") and claim is False:
                return
            s["changed"] = changed
            steps[nid] = s
            run.steps = steps
            await session.commit()


async def why_not_yours(org_id: str, agent_id: str, rel: str) -> str:
    """Why this agent may not change workspace file `rel` now ('' when it may)."""
    own, fenced = await fences(org_id, agent_id)
    if claims(rel, own):
        return ""
    for files, why in fenced:
        if claims(rel, files):
            mine = f" ({', '.join(own)})" if own else ""
            return (f"{rel} isn't yours to change now: {why}. Changing it under them breaks "
                    f"their work. Work in your own files{mine}, and put what {rel} needs in "
                    "your step's result: the step after yours fixes across files.")
    return ""


async def on_turn_complete(agent_id: str, org_id: str, turn_id: str | None, summary: str) -> None:
    """A turn that began with a step's message and ended without finish_step: its final
    reply is the step's output."""
    if not turn_id:
        return
    async with SessionLocal() as session:
        turn = await session.get(Turn, turn_id)
        triggers = set((turn.trigger_message_ids or []) if turn else [])
        rows = list((await session.execute(select(FlowRun).where(
            FlowRun.org_id == org_id, FlowRun.status == "running"))).scalars())
    for run in rows:
        for nid, s in (run.steps or {}).items():
            if s.get("status") == "running" and s.get("messageId") in triggers:
                try:
                    await finish_step(run.id, nid, summary or "(no output)",
                                      failed=not summary)
                except FlowError:
                    pass


async def cancel(run_id: str) -> FlowRun:
    async with _locks[run_id]:
        async with SessionLocal() as session:
            run = await session.get(FlowRun, run_id)
            if run is None:
                raise FlowError("run not found")
            if run.status == "running":
                steps = {k: dict(v) for k, v in (run.steps or {}).items()}
                for s in steps.values():
                    if s["status"] in ("pending", "running"):
                        s.update(status="skipped", finishedAt=utcnow().isoformat())
                run.steps = steps
                run.status = "cancelled"
                run.finished_at = utcnow()
                await session.commit()
    await bus.publish("flowrun.updated", run_to_dict(run), org_id=run.org_id)
    return run
