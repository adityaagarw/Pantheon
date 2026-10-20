"""Argus's oversight tools: read an organization's vital signs, progress and talk."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import (
    Agent,
    Approval,
    Channel,
    FeatureRequest,
    Message,
    Task,
    TaskEvent,
    Turn,
    UserInboxItem,
)
from app.services import oversight
from app.services.tasks import CLOSED, task_ref
from app.tools.base import I, S, ToolContext, obj
from app.tools.meta import _dump, _org, meta

STALE_HOURS = 6


def _ago(dt) -> str:
    if dt is None:
        return "never"
    mins = int((utcnow() - dt).total_seconds() // 60)
    return f"{mins} min ago" if mins < 120 else f"{mins // 60} h ago"


@meta("org_health", "Vital signs of an organization: agents in error / retrying / waiting for "
      "approval, repeated failures, stale or blocked tasks, overdue tasks, failed turns and "
      "spend in the last 24 hours, pending approvals and open feature requests.",
      obj({"org": S}, ["org"]))
async def org_health(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    now = utcnow()
    since = now - timedelta(hours=24)
    async with SessionLocal() as session:
        agents = (await session.execute(select(Agent).where(Agent.org_id == org.id))).scalars()
        agents = list(agents)
        open_tasks = list((await session.execute(select(Task).where(
            Task.org_id == org.id, Task.status.not_in(CLOSED)))).scalars())
        last_event = dict((await session.execute(select(
            TaskEvent.task_id, func.max(TaskEvent.created_at)).where(
            TaskEvent.task_id.in_([t.id for t in open_tasks] or [""])).group_by(
            TaskEvent.task_id))).all())
        turns = (await session.execute(select(
            Turn.status, func.count(), func.coalesce(func.sum(Turn.input_tokens
                                                              + Turn.output_tokens), 0),
            func.coalesce(func.sum(Turn.cost_usd), 0)).where(
            Turn.org_id == org.id, Turn.started_at >= since).group_by(Turn.status))).all()
        errors = (await session.execute(select(Turn).where(
            Turn.org_id == org.id, Turn.started_at >= since, Turn.error.is_not(None),
            Turn.error != "").order_by(Turn.started_at.desc()).limit(5))).scalars().all()
        approvals = await session.scalar(select(func.count()).select_from(Approval).where(
            Approval.org_id == org.id, Approval.status == "pending"))
        frs = await session.scalar(select(func.count()).select_from(FeatureRequest).where(
            FeatureRequest.org_id == org.id, FeatureRequest.status.in_(("open", "triaged"))))
    names = {a.id: a.name for a in agents}
    lines = [f"# {org.name} ({org.status}) — health at {now:%Y-%m-%d %H:%M} UTC"]
    trouble = [a for a in agents if a.runtime_status in ("error", "retrying", "cooling_down",
                                                         "awaiting_approval")
               or a.consecutive_failures]
    lines.append(f"Agents: {len(agents)} ({sum(a.runtime_status == 'working' for a in agents)} "
                 f"working, {sum(a.status != 'active' for a in agents)} paused/disabled)")
    for a in trouble:
        lines.append(f"- ⚠ {a.name}: {a.runtime_status}"
                     + (f", {a.consecutive_failures} consecutive failures"
                        if a.consecutive_failures else "")
                     + (f" — {a.runtime_detail[:200]}" if a.runtime_detail else ""))
    budget = int((org.settings or {}).get("daily_token_budget") or 0)
    tokens = sum(int(t[2]) for t in turns)
    lines.append("Turns (24 h): " + (", ".join(f"{s}={n}" for s, n, _, _ in turns) or "none")
                 + f"; tokens {tokens:,}" + (f" of {budget:,} budget" if budget else "")
                 + f"; cost ${sum(float(t[3]) for t in turns):.4f}")
    for t in errors:
        lines.append(f"- failed turn by {names.get(t.agent_id, t.agent_id)} "
                     f"{_ago(t.started_at)}: {(t.error or '')[:200]}")
    by_status: dict[str, int] = {}
    for t in open_tasks:
        by_status[t.status] = by_status.get(t.status, 0) + 1
    lines.append("Open tasks: " + (", ".join(f"{k}={v}" for k, v in by_status.items()) or "none"))
    for t in open_tasks:
        last = last_event.get(t.id) or t.updated_at
        flags = []
        if t.status == "blocked":
            flags.append("blocked")
        if t.status in ("in_progress", "review") and last and now - last > timedelta(
                hours=STALE_HOURS):
            flags.append(f"no activity for {_ago(last)}")
        if t.due_at and t.due_at < now:
            flags.append(f"overdue since {t.due_at:%Y-%m-%d %H:%M}")
        if t.status == "in_progress" and not t.assignee_id:
            flags.append("in progress with no assignee")
        if flags:
            lines.append(f"- ⚠ {task_ref(t)} {t.title} [{t.status}, "
                         f"{names.get(t.assignee_id or '', 'unassigned')}]: {', '.join(flags)}")
    lines.append(f"Pending approvals: {approvals or 0}; open feature requests: {frs or 0}")
    return "\n".join(lines)


@meta("list_org_tasks", "An organization's tasks with status, assignee, result and last "
      "activity. status: optional filter (todo, in_progress, review, blocked, done, ...).",
      obj({"org": S, "status": S, "limit": I}, ["org"]))
async def list_org_tasks(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    async with SessionLocal() as session:
        stmt = select(Task).where(Task.org_id == org.id)
        if args.get("status"):
            stmt = stmt.where(Task.status == args["status"])
        rows = (await session.execute(stmt.order_by(Task.updated_at.desc()).limit(
            min(int(args.get("limit") or 60), 200)))).scalars().all()
        names = dict((await session.execute(select(Agent.id, Agent.name).where(
            Agent.org_id == org.id))).all())
    if not rows:
        return "No tasks."
    return "\n".join(
        f"- {task_ref(t)} [{t.status}, {t.priority}] {t.title} — "
        f"{names.get(t.assignee_id or '', 'unassigned')}; updated {_ago(t.updated_at)}"
        + (f"; due {t.due_at:%Y-%m-%d}" if t.due_at else "")
        + (f"\n  acceptance: {t.acceptance[:200]}" if t.acceptance else "")
        + (f"\n  result: {t.result[:300]}" if t.result else "") for t in rows)


@meta("read_org_conversations", "Recent messages across an organization (channels, DMs between "
      "agents, speech), newest last. Optionally filter by channel key/name or an agent's name.",
      obj({"org": S, "channel": S, "agent": S, "limit": I}, ["org"]))
async def read_org_conversations(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    limit = min(int(args.get("limit") or 40), 150)
    async with SessionLocal() as session:
        agents = dict((await session.execute(select(Agent.id, Agent.name).where(
            Agent.org_id == org.id))).all())
        chans = {c.id: c for c in (await session.execute(select(Channel).where(
            Channel.org_id == org.id))).scalars()}
        stmt = select(Message).where(Message.org_id == org.id)
        if ref := (args.get("channel") or "").strip().lower():
            ids = [c.id for c in chans.values() if ref in (c.key.lower(), c.name.lower(),
                                                          "#" + c.name.lower())]
            stmt = stmt.where(Message.channel_id.in_(ids or [""]))
        if ref := (args.get("agent") or "").strip().lower():
            aid = next((i for i, n in agents.items() if n.lower() == ref), "")
            stmt = stmt.where(Message.sender_id == aid)
        rows = list(reversed((await session.execute(stmt.order_by(
            Message.created_at.desc()).limit(limit))).scalars().all()))
    names = agents | {"user": "the user", "system": "system"}
    out = []
    for m in rows:
        ch = chans.get(m.channel_id or "")
        where = (ch.key if ch and ch.kind == "channel" else
                 " ↔ ".join(names.get(x, x) for x in ch.members) if ch else m.kind)
        out.append(f"[{m.created_at:%m-%d %H:%M}] ({where}) {names.get(m.sender_id, m.sender_id)}: "
                   f"{m.content[:500]}")
    return "\n".join(out) or "No messages."


@meta("raise_alert", "Flag something the user must see: it goes to the organization's inbox "
      "with the given severity.",
      obj({"org": S, "title": S, "detail": S,
           "severity": {"type": "string", "enum": ["info", "warning", "critical"]}},
          ["org", "title", "detail"]))
async def raise_alert(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    severity = args.get("severity") or "warning"
    title = f"[{severity}] {args['title']}"[:300]
    async with SessionLocal() as session:
        item = UserInboxItem(org_id=org.id, kind="alert", ref_id=ctx.agent.id,
                             title=f"{title}\n{args['detail']}"[:4000])
        session.add(item)
        await session.commit()
    await bus.publish("alert.raised", {"id": item.id, "title": args["title"],
                                       "detail": args["detail"], "severity": severity,
                                       "by": ctx.agent.name}, org_id=org.id)
    return f"Alert raised in {org.name}'s inbox."


@meta("watch_org", "Keep an eye on an organization: you'll be woken every N minutes (min 5) "
      "to check it. focus: what to watch for (goals, deadlines, risks).",
      obj({"org": S, "every_minutes": I, "focus": S}, ["org", "every_minutes"]))
async def watch_org(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    w = await oversight.set_watch(org.id, org.name, int(args["every_minutes"]),
                                  args.get("focus", ""))
    return f"Watching {org.name} every {w['everyMinutes']} minutes."


@meta("unwatch_org", "Stop the scheduled checks of an organization.", obj({"org": S}, ["org"]))
async def unwatch_org(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    return (f"Stopped watching {org.name}." if await oversight.remove_watch(org.id)
            else f"{org.name} wasn't being watched.")


@meta("list_watches", "The organizations you're watching and their schedules.", obj({}))
async def list_watches(args: dict, ctx: ToolContext) -> str:
    ws = await oversight.watches()
    return _dump(list(ws.values())) if ws else "Not watching any organization."
