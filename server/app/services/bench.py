"""Benchmarking organizations with SwarmBench (``server/bench``, vendored).

An *attempt* gives one prompt to a temporary copy of an organization: same agents,
personas, tools, models, reporting lines and channels, with a given work folder as its
workspace and every tool approved automatically (nobody is there to click). The lead
gets the prompt as a message from the user, and the goal as a task on the board; the
attempt ends when the lead moves that task on (finished), when the whole team has been
quiet for a while, or when the time is up. It reports tokens, cost, turns, tool calls,
messages and agents, then deletes the copy (the work folder stays, for grading).

A *run* (``BenchRun``) does that for a set of SwarmBench tasks: copies each task's
starting repo into a fresh work folder, builds the task's prompt (with its workflow),
runs an attempt and grades the folder with SwarmBench's grader. The SwarmBench CLI
can also drive attempts over the API (its ``pantheon`` harness).

Note on fairness: agents run commands in the backend container, where the benchmark
(and its hidden tests) also lives. Nothing points them there, but a determined agent
could look; the SwarmBench README explains strict setups.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from sqlalchemy import func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.events import bus
from app.models import (
    Agent,
    Approval,
    BenchRun,
    Delivery,
    LlmCall,
    Message,
    Org,
    Relationship,
    Task,
    ToolCall,
    Turn,
)
from app.services.comms import Sender

BENCH_ROOT = Path(__file__).resolve().parents[2] / "bench"
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from swarmbench import grader as sb_grader  # noqa: E402
from swarmbench import prompt as sb_prompt  # noqa: E402
from swarmbench import tasks as sb_tasks  # noqa: E402
from swarmbench import utils as sb_utils  # noqa: E402
from swarmbench import workflows as sb_workflows  # noqa: E402

log = logging.getLogger(__name__)
BUSY = ("working", "queued", "awaiting_approval", "retrying")
OUTSIDE_HELP = ("ask_zeus", "request_feature")
POLL = 3.0


class BenchError(ValueError):
    pass


# --- what can be run --------------------------------------------------------------------------


def task_catalog() -> list[dict[str, Any]]:
    return [{"id": t.id, "title": t.title, "category": t.category, "difficulty": t.difficulty,
             "workflow": t.workflow, "summary": t.summary, "minutes": t.time_limit_minutes}
            for t in sb_tasks.load_tasks()]


def workflow_catalog() -> list[dict[str, Any]]:
    out = []
    for name in sb_workflows.list_workflows():
        w = sb_workflows.load_workflow(name)
        out.append({"name": name, "summary": w.summary, "roles": [r["name"] for r in w.roles]})
    return out


def bench_folder() -> Path:
    roots = settings.workspace_root_paths
    if not roots:
        raise BenchError("no workspace root is configured (PANTHEON_WORKSPACE_ROOTS)")
    return roots[0] / "swarmbench"


# --- attempts -----------------------------------------------------------------------------------


@dataclass
class Attempt:
    id: str
    org_id: str
    org_name: str
    label: str
    status: str = "starting"  # starting running done timeout failed cancelled
    detail: str = ""
    clone_id: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    cancel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "orgId": self.org_id, "orgName": self.org_name, "label": self.label,
                "status": self.status, "detail": self.detail, "cloneId": self.clone_id,
                "metrics": self.metrics, "seconds": round(time.monotonic() - self.started, 1)}


ATTEMPTS: dict[str, Attempt] = {}


async def find_org(ref: str) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, ref)
        if org is None:
            org = (await session.execute(select(Org).where(
                func.lower(Org.name) == str(ref).strip().lower(), Org.kind == "user"))).scalars().first()
    if org is None or org.kind == "system":
        raise BenchError(f"no organization '{ref}'")
    return org


async def _clone(org: Org, label: str, workspace: str) -> Org:
    """A copy of the organization to run one attempt on: same models, tools always approved."""
    from app.services import orgs

    definition = await orgs.export_org(org.id)
    async with SessionLocal() as session:
        originals = {a.name: a for a in (await session.execute(select(Agent).where(
            Agent.org_id == org.id, Agent.is_supervisor.is_(False)))).scalars()}
    for spec in definition.get("agents") or []:
        source = originals.get(spec.get("name"))
        if source is not None:
            spec["model"] = dict(source.model or {})  # export leaves the provider out; keep it
        spec["tools"] = [{**(t if isinstance(t, dict) else {"name": t}), "approval": "auto"}
                         for t in spec.get("tools") or [] if
                         (t.get("approval") if isinstance(t, dict) else None) != "deny"
                         and (t.get("name") if isinstance(t, dict) else t) not in OUTSIDE_HELP]
        # The organization works alone: no help (or hires) from Zeus, whose work and
        # tokens would not be counted.
        spec["tools"] += [{"name": n, "approval": "deny"} for n in OUTSIDE_HELP]
    settings_ = dict(definition.get("settings") or {})
    if (org.settings or {}).get("default_model"):
        settings_["default_model"] = org.settings["default_model"]
    settings_["shell_approval"] = "auto"
    definition["settings"] = settings_
    definition.pop("activities", None)
    clone = await orgs.import_org(definition, name=f"Bench · {org.name} · {label}"[:120],
                                  workspace=workspace)
    async with SessionLocal() as session:
        row = await session.get(Org, clone.id)
        assert row is not None
        row.kind = "bench"  # out of the organization lists
        await session.commit()
    return clone


async def _lead(org_id: str, name: str | None) -> Agent:
    async with SessionLocal() as session:
        agents = list((await session.execute(select(Agent).where(
            Agent.org_id == org_id, Agent.is_supervisor.is_(False)).order_by(Agent.created_at))).scalars())
        managed = set((await session.execute(select(Relationship.to_id).where(
            Relationship.org_id == org_id, Relationship.kind == "manages"))).scalars())
    if not agents:
        raise BenchError("the organization has no agents")
    if name:
        for a in agents:
            if a.name.lower() == name.strip().lower():
                return a
        raise BenchError(f"no agent '{name}' to lead")
    managers = {a.id for a in agents} - managed
    return next((a for a in agents if a.id in managers), agents[0])


async def _quiet(org_id: str) -> tuple[bool, str]:
    async with SessionLocal() as session:
        statuses = dict((await session.execute(select(Agent.name, Agent.runtime_status).where(
            Agent.org_id == org_id))).all())
        waiting = await session.scalar(select(func.count()).select_from(Delivery).join(
            Agent, Agent.id == Delivery.agent_id).where(
            Agent.org_id == org_id, Delivery.status.in_(("pending", "claimed"))))
    busy = [n for n, s in statuses.items() if s in BUSY]
    if busy:
        return False, f"working: {', '.join(busy)}{await _latest(org_id)}"
    if waiting:
        return False, f"{waiting} message(s) still to be read"
    return True, "quiet"


async def _latest(org_id: str) -> str:
    """What the team did last, e.g. ' · last: Atlas ran run_command 12 s ago'."""
    async with SessionLocal() as session:
        row = (await session.execute(select(Agent.name, ToolCall.name, ToolCall.created_at).join(
            Agent, Agent.id == ToolCall.agent_id).where(ToolCall.org_id == org_id).order_by(
            ToolCall.created_at.desc()).limit(1))).first()
    if row is None:
        return " · thinking"
    ago = max(0, round((utcnow() - row[2]).total_seconds()))
    return f" · last: {row[0]} used {row[1]} {ago} s ago"


async def _approve_pending(org_id: str) -> None:
    from app.agents.runtime import runtime

    async with SessionLocal() as session:
        ids = (await session.execute(select(Approval.id).where(
            Approval.org_id == org_id, Approval.status == "pending"))).scalars().all()
    for aid in ids:
        try:
            await runtime.decide(aid, True, "approved by the benchmark")
        except KeyError:
            continue


MODEL_DEAD = "the model isn't answering"


async def _model_dead(org_id: str) -> str | None:
    """Two failed model calls and none that worked: the model server is down, stop waiting."""
    async with SessionLocal() as session:
        ok = await session.scalar(select(func.count()).select_from(LlmCall).where(
            LlmCall.org_id == org_id, LlmCall.error.is_(None)))
        failed = (await session.execute(select(LlmCall.error).where(
            LlmCall.org_id == org_id, LlmCall.error.is_not(None)).order_by(
            LlmCall.created_at.desc()))).scalars().all()
    if ok or len(failed) < 2:
        return None
    return f"{MODEL_DEAD} ({failed[0][:200]})"


async def check_models(org: Org) -> None:
    """Before a run: every model the organization's agents use must answer, or nothing starts."""
    from app.llm.providers import list_remote_models, resolve_for_agent
    from app.models import Provider

    async with SessionLocal() as session:
        agents = list((await session.execute(select(Agent).where(
            Agent.org_id == org.id, Agent.is_supervisor.is_(False)))).scalars())
    checked: set[str] = set()
    for agent in agents:
        try:
            model = await resolve_for_agent(agent, org)
        except Exception as e:  # noqa: BLE001
            raise BenchError(f"{agent.name} has no usable model: {e}") from None
        if model.provider_id in checked:
            continue
        checked.add(model.provider_id)
        async with SessionLocal() as session:
            provider = await session.get(Provider, model.provider_id)
        if provider is None or provider.type == "mock":
            continue
        try:
            await asyncio.wait_for(list_remote_models(provider), timeout=20)
        except Exception as e:  # noqa: BLE001
            reason = ("no answer in 20 s" if isinstance(e, TimeoutError)
                      else f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
            raise BenchError(f"The model server '{provider.name}' ({provider.base_url or provider.type}) "
                             f"isn't answering ({reason}). Start it, check it with Test in "
                             "Settings > LLM providers, then run the benchmark again.") from None


async def usage(org_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        tin, tout, cost = (await session.execute(select(
            func.coalesce(func.sum(LlmCall.input_tokens), 0),
            func.coalesce(func.sum(LlmCall.output_tokens), 0),
            func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.org_id == org_id))).one()
        turns = await session.scalar(select(func.count()).select_from(Turn).where(Turn.org_id == org_id))
        tools = await session.scalar(select(func.count()).select_from(ToolCall).where(
            ToolCall.org_id == org_id))
        messages = await session.scalar(select(func.count()).select_from(Message).where(
            Message.org_id == org_id, Message.sender_type == "agent"))
        agents = await session.scalar(select(func.count()).select_from(Agent).where(
            Agent.org_id == org_id, Agent.is_supervisor.is_(False)))
        active = await session.scalar(select(func.count(func.distinct(Turn.agent_id))).where(
            Turn.org_id == org_id))
    return {"input_tokens": int(tin), "output_tokens": int(tout), "cost_usd": round(float(cost), 4),
            "turns": int(turns or 0), "tool_calls": int(tools or 0), "messages": int(messages or 0),
            "agents": int(agents or 0), "agents_active": int(active or 0)}


async def _stop(org_id: str) -> None:
    from app.agents.runtime import runtime
    from app.services import orgs

    await orgs.update_org(org_id, {"status": "paused"})
    async with SessionLocal() as session:
        ids = (await session.execute(select(Agent.id).where(Agent.org_id == org_id))).scalars().all()
    for aid in ids:
        try:
            await runtime.stop_turn(aid)
        except Exception:  # noqa: BLE001 - stopping is best effort
            pass


FINISHED = ("review", "done", "cancelled")  # the lead says the goal is finished
FINISH_GRACE = 20.0  # seconds the team gets to wrap up after that


async def _goal(org_id: str, leader: Agent, label: str) -> Task:
    """The goal as a task on the copy's board, assigned to the lead: moving it on is how the
    team says it's done (without it, the only signal is the team going quiet)."""
    from app.services import tasks

    goal = await tasks.create_task(org_id, Sender.user(), title=f"Goal: {label}"[:300],
                                   description="The user's message has the full goal.",
                                   acceptance="Everything the user's message asks for is done.")
    async with SessionLocal() as session:  # assigned quietly: the message is the wake-up
        row = await session.get(Task, goal.id)
        assert row is not None
        row.assignee_id, row.status = leader.id, "todo"
        await session.commit()
        await session.refresh(row)
        return row


async def _goal_status(task_id: str) -> str | None:
    async with SessionLocal() as session:
        row = await session.get(Task, task_id)
        return row.status if row else None


async def run_attempt(att: Attempt, prompt: str, workspace: str, *, lead: str | None = None,
                      timeout: float = 3600, idle_seconds: float = 45, keep_org: bool = False) -> Attempt:
    """Run one prompt on a copy of the organization, until the lead finishes the goal, the
    team is quiet, or time is up."""
    from app.services import comms, orgs
    from app.services.tasks import task_ref
    from app.tools.resolve import resolve_tools

    clone_id = None
    try:
        source = await find_org(att.org_id)
        clone = await _clone(source, att.label, workspace)
        clone_id = att.clone_id = clone.id
        leader = await _lead(clone.id, lead)
        att.status, att.detail = "running", f"{leader.name} has the task"
        goal = None
        if "update_task" in (await resolve_tools(clone, leader)).tools:
            goal = await _goal(clone.id, leader, att.label)
            ref = task_ref(goal)
            prompt += (f"\n\n---\nThis goal is {ref} on the board, assigned to you. When the "
                       f"work is complete, put a short result on {ref} and move it to review. "
                       "That tells me the work is finished.")
        await comms.send_direct(clone.id, Sender.user(), leader.id, prompt)
        quiet_since: float | None = None
        finished_at: float | None = None
        deadline = time.monotonic() + timeout
        while True:
            await asyncio.sleep(POLL)
            if att.cancel:
                att.status, att.detail = "cancelled", "cancelled"
                break
            if time.monotonic() > deadline:
                att.status, att.detail = "timeout", f"out of time after {round(timeout)} s"
                break
            await _approve_pending(clone.id)
            if dead := await _model_dead(clone.id):
                att.status, att.detail = "failed", dead
                break
            quiet, why = await _quiet(clone.id)
            att.metrics = await usage(clone.id)
            if finished_at is None and goal and await _goal_status(goal.id) in FINISHED:
                finished_at = time.monotonic()
            if finished_at is not None:
                if quiet or time.monotonic() - finished_at >= FINISH_GRACE:
                    att.status, att.detail = "done", f"{leader.name} finished the goal"
                    break
                att.detail = f"{leader.name} finished the goal; wrapping up · {why}"
                continue
            if not quiet:
                quiet_since, att.detail = None, why
                continue
            quiet_since = quiet_since or time.monotonic()
            att.detail = f"quiet for {round(time.monotonic() - quiet_since)} s"
            if att.metrics["turns"] and time.monotonic() - quiet_since >= idle_seconds:
                att.status, att.detail = "done", "the team stopped working"
                break
    except Exception as e:  # noqa: BLE001 - an attempt reports its failure, it doesn't raise
        log.exception("bench attempt %s failed", att.id)
        att.status, att.detail = "failed", f"{type(e).__name__}: {e}"
    finally:
        if clone_id:
            await _stop(clone_id)
            att.metrics = await usage(clone_id)
            att.metrics["wall_seconds"] = round(time.monotonic() - att.started, 1)
            if not keep_org:
                await orgs.delete_org(clone_id)
    return att


def start_attempt(org: Org, prompt: str, workspace: str, *, label: str, lead: str | None,
                  timeout: float, idle_seconds: float, keep_org: bool) -> Attempt:
    """For the SwarmBench CLI: start an attempt in the background and return it."""
    path = Path(workspace)
    roots = settings.workspace_root_paths
    if not any(path.resolve() == r or r in path.resolve().parents for r in roots):
        raise BenchError(f"{workspace} is not inside a workspace root ({', '.join(map(str, roots))})")
    if not path.is_dir():
        raise BenchError(f"{workspace} does not exist (the work folder must be visible to Pantheon)")
    att = Attempt(id=new_id("att"), org_id=org.id, org_name=org.name, label=label)
    ATTEMPTS[att.id] = att
    asyncio.create_task(run_attempt(att, prompt, str(path), lead=lead, timeout=timeout,
                                    idle_seconds=idle_seconds, keep_org=keep_org))
    return att


# --- runs ----------------------------------------------------------------------------------------


RUNS: dict[str, asyncio.Task] = {}
CURRENT: dict[str, list[Attempt]] = {}


def run_to_dict(r: BenchRun) -> dict[str, Any]:
    results = list(r.results or [])
    scores = [x["score"] for x in results]
    cost = sum(x.get("metrics", {}).get("cost_usd") or 0 for x in results)
    tokens = sum((x.get("metrics", {}).get("input_tokens") or 0)
                 + (x.get("metrics", {}).get("output_tokens") or 0) for x in results)
    config = r.config or {}
    total = len(config.get("tasks") or []) * int(config.get("repeat") or 1)
    return {"id": r.id, "orgId": r.org_id, "orgName": r.org_name, "status": r.status,
            "config": config, "results": results, "detail": r.detail,
            "progress": {"done": len(results), "total": total,
                         "current": [a.to_dict() for a in CURRENT.get(r.id, [])]},
            "summary": {"meanScore": round(mean(scores), 1) if scores else None,
                        "fullScores": sum(1 for s in scores if s >= 99.95), "costUsd": round(cost, 4),
                        "tokens": tokens,
                        "wallSeconds": round(sum(x["run"]["wall_seconds"] for x in results), 1),
                        "timeouts": sum(1 for x in results if x["run"]["timed_out"])},
            "createdAt": r.created_at.isoformat() if r.created_at else None,
            "finishedAt": r.finished_at.isoformat() if r.finished_at else None}


async def _save(run_id: str, **values: Any) -> dict[str, Any]:
    async with SessionLocal() as session:
        r = await session.get(BenchRun, run_id)
        assert r is not None
        for k, v in values.items():
            setattr(r, k, v)
        await session.commit()
        await session.refresh(r)
        data = run_to_dict(r)
    await bus.publish("bench.updated", data)
    return data


async def start_run(org_ref: str, *, tasks: list[str], workflow: str = "task", repeat: int = 1,
                    timeout_minutes: float | None = None, jobs: int = 1, lead: str | None = None,
                    idle_seconds: float = 45) -> dict[str, Any]:
    org = await find_org(org_ref)
    try:
        chosen = sb_tasks.load_tasks(",".join(tasks) if tasks else "all")
        for t in chosen:
            sb_workflows.resolve_workflow(workflow, t)
    except sb_tasks.BenchError as e:
        raise BenchError(str(e).removeprefix("error: ")) from None
    if not 1 <= repeat <= 10 or not 1 <= jobs <= 8:
        raise BenchError("repeat must be 1-10 and jobs 1-8")
    await check_models(org)
    config = {"tasks": [t.id for t in chosen], "workflow": workflow, "repeat": repeat,
              "timeoutMinutes": timeout_minutes, "jobs": jobs, "lead": lead, "idleSeconds": idle_seconds}
    async with SessionLocal() as session:
        r = BenchRun(org_id=org.id, org_name=org.name, status="running", config=config, results=[])
        session.add(r)
        await session.commit()
        await session.refresh(r)
        data = run_to_dict(r)
    RUNS[r.id] = asyncio.create_task(_run(r.id, org, chosen, config), name=f"bench:{r.id}")
    await bus.publish("bench.updated", data)
    return data


async def _run(run_id: str, org: Org, chosen: list, config: dict[str, Any]) -> None:
    repeat = int(config["repeat"])
    work = [(t, t.id if repeat == 1 else f"{t.id}__r{n}") for t in chosen for n in range(1, repeat + 1)]
    gate = asyncio.Semaphore(int(config.get("jobs") or 1))
    lock = asyncio.Lock()
    CURRENT[run_id] = []

    stopped: dict[str, str] = {}

    async def one(task: Any, key: str) -> None:
        async with gate:
            if stopped or (await _status(run_id)) in ("cancelled", "failed"):
                return
            folder = bench_folder() / run_id / key
            await asyncio.to_thread(_fresh_folder, task.repo_path, folder)
            wf = sb_workflows.resolve_workflow(config["workflow"], task)
            text = sb_prompt.build_prompt(task, wf, str(folder))
            timeout = float(config.get("timeoutMinutes") or task.time_limit_minutes * 2) * 60
            att = Attempt(id=new_id("att"), org_id=org.id, org_name=org.name, label=key)
            CURRENT[run_id].append(att)
            await _save(run_id, detail=f"running {key}")
            await run_attempt(att, text, str(folder), lead=config.get("lead"), timeout=timeout,
                              idle_seconds=float(config.get("idleSeconds") or 45))
            if att.detail.startswith(MODEL_DEAD):
                CURRENT[run_id].remove(att)
                stopped["why"] = f"Stopped: {att.detail}. Start the model server and run again."
                await _save(run_id, status="failed", detail=stopped["why"], finished_at=utcnow())
                return
            grade = await asyncio.to_thread(sb_grader.grade_task, task, folder, wf)
            result = {"task_id": task.id, "task_key": key, "title": task.title,
                      "category": task.category, "difficulty": task.difficulty,
                      "workflow": wf.name if wf else None, "workspace": str(folder),
                      "run": {"status": att.status, "detail": att.detail,
                              "timed_out": att.status == "timeout",
                              "wall_seconds": att.metrics.get("wall_seconds", 0)},
                      "metrics": att.metrics, **grade}
            CURRENT[run_id].remove(att)
            async with lock:
                async with SessionLocal() as session:
                    r = await session.get(BenchRun, run_id)
                    assert r is not None
                    results = [*(r.results or []), result]
                await _save(run_id, results=sorted(results, key=lambda x: x["task_key"]),
                            detail=f"graded {key}: {grade['score']}")

    try:
        await asyncio.gather(*(one(t, k) for t, k in work))
        if (await _status(run_id)) not in ("cancelled", "failed"):
            await _save(run_id, status="done", detail="finished", finished_at=utcnow())
    except Exception as e:  # noqa: BLE001
        log.exception("bench run %s failed", run_id)
        await _save(run_id, status="failed", detail=f"{type(e).__name__}: {e}", finished_at=utcnow())
    finally:
        CURRENT.pop(run_id, None)
        RUNS.pop(run_id, None)


def _fresh_folder(repo: Path, folder: Path) -> None:
    import shutil

    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    sb_utils.copy_tree(repo, folder)


async def _status(run_id: str) -> str | None:
    async with SessionLocal() as session:
        r = await session.get(BenchRun, run_id)
        return r.status if r else None


async def cancel_run(run_id: str) -> dict[str, Any]:
    for att in CURRENT.get(run_id, []):
        att.cancel = True
    return await _save(run_id, status="cancelled", detail="cancelled", finished_at=utcnow())


async def recover() -> None:
    """After a restart, runs that were going can't continue: mark them interrupted."""
    async with SessionLocal() as session:
        rows = (await session.execute(select(BenchRun).where(BenchRun.status == "running"))).scalars().all()
        for r in rows:
            r.status, r.detail, r.finished_at = "failed", "interrupted by a restart", utcnow()
        clones = (await session.execute(select(Org.id).where(Org.kind == "bench"))).scalars().all()
        await session.commit()
    from app.services import orgs

    for cid in clones:  # copies left behind by an interrupted attempt
        await orgs.delete_org(cid)
