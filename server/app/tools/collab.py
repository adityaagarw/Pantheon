"""Collaboration tools: messaging, channels, tasks, memory, feature requests."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Agent, Channel, FeatureRequest, Relationship
from app.services import comms, memory, tasks
from app.services.comms import USER, CommsError
from app.services.tasks import STATUSES, TaskError, task_ref
from app.tools.base import B, I, S, ToolContext, ToolError, arr, obj, tool


async def _names(org_id: str) -> dict[str, str]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Agent.id, Agent.name).where(Agent.org_id == org_id)))
        out = {r[0]: r[1] for r in rows.all()}
    out[USER] = "user"
    out["system"] = "system"
    return out


# --- messaging --------------------------------------------------------------------


@tool(
    "send_message",
    """Send a direct message to one colleague (by name) or to "user" (the human
you work for). Use it to ask questions, hand off information, report progress
or reply to someone. The recipient reads it in their inbox; replies come back
to your inbox later, so end your turn instead of waiting.""",
    obj({"to": S, "content": S,
         "task": {"type": "string", "description": "Optional related task ref, e.g. T-12"}},
        ["to", "content"]),
    category="communication",
)
async def send_message(args: dict, ctx: ToolContext) -> str:
    try:
        task_id = None
        if args.get("task"):
            async with SessionLocal() as session:
                t = await tasks.find_task(session, ctx.org.id, args["task"])
                task_id = t.id if t else None
        msg = await comms.send_direct(ctx.org.id, ctx.sender, args["to"], args["content"],
                                      task_id=task_id)
    except CommsError as e:
        raise ToolError(str(e)) from None
    return f"Sent to {args['to']} (message {msg.id})."


@tool(
    "post_message",
    """Post a message to a channel (e.g. "#general"). Every member of the channel
receives it. Mention people with @Name to get their attention in
mentions-only channels.""",
    obj({"channel": S, "content": S}, ["channel", "content"]),
    category="communication",
)
async def post_message(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        ch = await comms.find_channel(session, ctx.org.id, args["channel"])
    if ch is None or ch.kind == "dm":
        raise ToolError(f"no channel '{args['channel']}' (use list_channels)")
    if ctx.agent.id not in ch.members:
        raise ToolError(f"you are not a member of {ch.key}")
    try:
        msg = await comms.post(ctx.org.id, ctx.sender, args["content"], channel=ch)
    except CommsError as e:
        raise ToolError(str(e)) from None
    return f"Posted to {ch.key} (message {msg.id})."


@tool(
    "read_channel",
    """Read recent messages of a channel you belong to, or your DM history with a
colleague (pass their name, or "user").""",
    obj({"channel": S, "limit": I}, ["channel"]),
    category="communication",
)
async def read_channel(args: dict, ctx: ToolContext) -> str:
    ref = args["channel"]
    async with SessionLocal() as session:
        ch = await comms.find_channel(session, ctx.org.id, ref)
        if ch is None:
            other = USER if ref.lower() == USER else None
            if other is None:
                a = await comms.resolve_agent(session, ctx.org.id, ref)
                other = a.id if a else None
            if other is not None:
                key = comms.dm_key(ctx.agent.id, other)
                ch = (await session.execute(select(Channel).where(
                    Channel.org_id == ctx.org.id, Channel.key == key))).scalar_one_or_none()
    if ch is None:
        raise ToolError(f"no channel or conversation '{ref}'")
    if ctx.agent.id not in ch.members:
        raise ToolError(f"you are not a member of {ch.key}")
    limit = max(1, min(int(args.get("limit") or 30), 100))
    msgs = await comms.channel_history(ctx.org.id, ch.id, limit=limit)
    names = await _names(ctx.org.id)
    if not msgs:
        return f"{ch.key}: no messages yet."
    lines = [f"[{m.created_at:%Y-%m-%d %H:%M}] {names.get(m.sender_id, m.sender_id)}: {m.content}"
             for m in msgs]
    return f"{ch.key} (last {len(msgs)}):\n" + "\n".join(lines)


@tool("list_channels", "List the org's channels, their topics and whether you are a member.",
      obj({}), category="communication")
async def list_channels(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Channel).where(
            Channel.org_id == ctx.org.id, Channel.kind == "channel",
            Channel.archived.is_(False)))).scalars().all()
    if not rows:
        return "No channels yet. Create one with create_channel."
    names = await _names(ctx.org.id)
    return "\n".join(
        f"{c.key}{' (member)' if ctx.agent.id in c.members else ''} — {c.topic or 'no topic'}; "
        f"members: {', '.join(names.get(m, m) for m in c.members)}" for c in rows
    )


@tool(
    "create_channel",
    "Create a channel for a team, project or topic and add members (names, or \"user\").",
    obj({"name": S, "members": arr(S), "topic": S}, ["name", "members"]),
    category="communication",
)
async def create_channel(args: dict, ctx: ToolContext) -> str:
    try:
        ch = await comms.create_channel(ctx.org.id, args["name"], args.get("members") or [],
                                        topic=args.get("topic", ""), created_by=ctx.agent.id)
    except CommsError as e:
        raise ToolError(str(e)) from None
    return f"Created {ch.key} with {len(ch.members)} members."


@tool(
    "list_colleagues",
    "List everyone in your organization: name, role, team, and how they relate to you.",
    obj({}),
    category="communication",
)
async def list_colleagues(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        agents = (await session.execute(select(Agent).where(
            Agent.org_id == ctx.org.id, Agent.status != "disabled"))).scalars().all()
        rels = (await session.execute(select(Relationship).where(
            Relationship.org_id == ctx.org.id))).scalars().all()
    lines = []
    for a in agents:
        if a.id == ctx.agent.id:
            continue
        rel = []
        for r in rels:
            if r.from_id == ctx.agent.id and r.to_id == a.id:
                rel.append({"manages": "you manage them"}.get(r.kind, r.label or r.kind))
            elif r.to_id == ctx.agent.id and r.from_id == a.id:
                rel.append({"manages": "your manager"}.get(r.kind, r.label or r.kind))
        lines.append(f"- {a.name} — {a.role or 'no role'}{f' ({a.team})' if a.team else ''}"
                     f"{'; ' + ', '.join(rel) if rel else ''}; status {a.runtime_status}")
    return "\n".join(lines) or "You are the only member of this organization."


# --- tasks ------------------------------------------------------------------------


def _fmt_task(t, names: dict[str, str]) -> str:
    return (f"{task_ref(t)} [{t.status}] ({t.priority}) {t.title}"
            f" — assignee: {names.get(t.assignee_id or '', t.assignee_id) or 'unassigned'}")


@tool(
    "create_task",
    """Create a task on the org's board. Assign it to a colleague (or yourself) to
delegate work; the assignee is notified. Break big goals into subtasks with
`parent`, and express ordering with `depends_on`. Write clear acceptance
criteria so the reviewer can verify it.""",
    obj({
        "title": S, "description": S, "acceptance": S,
        "assignee": {"type": "string", "description": "Colleague name, or omit for backlog"},
        "reviewer": S,
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "parent": {"type": "string", "description": "Parent task ref, e.g. T-3"},
        "depends_on": arr(S), "labels": arr(S), "board": S,
    }, ["title"]),
    category="tasks",
)
async def create_task(args: dict, ctx: ToolContext) -> str:
    try:
        t = await tasks.create_task(
            ctx.org.id, ctx.sender, title=args["title"], description=args.get("description", ""),
            acceptance=args.get("acceptance", ""), assignee=args.get("assignee"),
            reviewer=args.get("reviewer"), priority=args.get("priority", "normal"),
            parent=args.get("parent"), depends_on=args.get("depends_on"),
            labels=args.get("labels"), board_id=args.get("board"),
        )
    except TaskError as e:
        raise ToolError(str(e)) from None
    return f"Created {task_ref(t)}: {t.title} [{t.status}]."


@tool(
    "update_task",
    f"""Update a task: move it between statuses ({", ".join(STATUSES)}), reassign it,
record the result, or add a comment. Move your work to "review" with a result
summary when done (the reviewer is notified); reviewers move it to "done" or
back to "in_progress" with a comment.""",
    obj({
        "task": {"type": "string", "description": "Task ref, e.g. T-12"},
        "status": {"type": "string", "enum": list(STATUSES)},
        "assignee": S, "unassign": B, "reviewer": S,
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "result": {"type": "string", "description": "What was produced / outcome"},
        "comment": S, "title": S, "description": S, "acceptance": S,
        "depends_on": arr(S), "labels": arr(S), "watch": B,
    }, ["task"]),
    category="tasks",
)
async def update_task(args: dict, ctx: ToolContext) -> str:
    fields = {k: v for k, v in args.items() if k != "task"}
    try:
        t = await tasks.update_task(ctx.org.id, ctx.sender, args["task"], **fields)
    except TaskError as e:
        raise ToolError(str(e)) from None
    return f"Updated {task_ref(t)} [{t.status}]."


@tool(
    "list_tasks",
    "List tasks. By default: open tasks assigned to you. Use scope=all for the whole board.",
    obj({"scope": {"type": "string", "enum": ["mine", "all", "reported"]},
         "status": arr({"type": "string", "enum": list(STATUSES)}),
         "assignee": S, "include_closed": B}),
    category="tasks",
)
async def list_tasks(args: dict, ctx: ToolContext) -> str:
    scope = args.get("scope", "mine")
    assignee = None
    if args.get("assignee"):
        async with SessionLocal() as session:
            a = await comms.resolve_agent(session, ctx.org.id, args["assignee"])
        if a is None:
            raise ToolError(f"no colleague named '{args['assignee']}'")
        assignee = a.id
    elif scope == "mine":
        assignee = ctx.agent.id
    rows = await tasks.list_tasks(ctx.org.id, assignee=assignee, status=args.get("status"),
                                  include_closed=bool(args.get("include_closed")))
    if scope == "reported":
        rows = [t for t in rows if t.reporter_id == ctx.agent.id]
    names = await _names(ctx.org.id)
    if not rows:
        return "No matching tasks."
    return "\n".join(_fmt_task(t, names) for t in rows[:100])


@tool("get_task", "Full details of a task, including its history and comments.",
      obj({"task": S}, ["task"]), category="tasks")
async def get_task(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        t = await tasks.find_task(session, ctx.org.id, args["task"])
        if t is None:
            raise ToolError(f"task '{args['task']}' not found")
        subtasks = (await session.execute(select(tasks.Task).where(
            tasks.Task.parent_id == t.id))).scalars().all()
        deps = [await session.get(tasks.Task, d) for d in t.depends_on or []]
    names = await _names(ctx.org.id)
    timeline = await tasks.task_timeline(ctx.org.id, t.id)
    lines = [
        f"{task_ref(t)}: {t.title}",
        f"status: {t.status}; priority: {t.priority}; assignee: "
        f"{names.get(t.assignee_id or '', 'unassigned')}; reporter: "
        f"{names.get(t.reporter_id, t.reporter_id)}; reviewer: "
        f"{names.get(t.reviewer_id or '', '-')}",
        f"description: {t.description or '-'}",
        f"acceptance: {t.acceptance or '-'}",
        f"result: {t.result or '-'}",
    ]
    if deps:
        lines.append("depends on: " + ", ".join(f"{task_ref(d)} [{d.status}]" for d in deps if d))
    if subtasks:
        lines.append("subtasks: " + ", ".join(f"{task_ref(s)} [{s.status}]" for s in subtasks))
    for ev in timeline[-20:]:
        who = names.get(ev["actorId"], ev["actorId"])
        if ev["kind"] == "comment":
            lines.append(f"  💬 {who}: {ev['data'].get('text', '')}")
        elif ev["kind"] == "status":
            lines.append(f"  → {who} moved {ev['data'].get('from')} → {ev['data'].get('to')}")
        elif ev["kind"] == "assignee":
            lines.append(f"  → {who} assigned to {names.get(ev['data'].get('to') or '', '-')}")
    return "\n".join(lines)


# --- memory -----------------------------------------------------------------------


@tool(
    "remember",
    """Save something to long-term memory so you can recall it in future turns
(decisions, facts, preferences, lessons). shared=true makes it visible to the
whole organization.""",
    obj({"content": S, "shared": B}, ["content"]),
    category="memory",
)
async def remember(args: dict, ctx: ToolContext) -> str:
    m = await memory.add(ctx.org.id, args["content"],
                         agent_id=None if args.get("shared") else ctx.agent.id)
    return f"Saved to {'shared' if args.get('shared') else 'your'} memory ({m.id})."


@tool("recall", "Search your memory and the org's shared knowledge.",
      obj({"query": S, "limit": I}, ["query"]), category="memory")
async def recall(args: dict, ctx: ToolContext) -> str:
    rows = await memory.search(ctx.org.id, args["query"], agent_id=ctx.agent.id,
                               k=max(1, min(int(args.get("limit") or 8), 30)))
    if not rows:
        return "Nothing relevant found."
    return "\n".join(
        f"- [{'shared' if m.agent_id is None else 'mine'} {m.created_at:%Y-%m-%d}] {m.content}"
        for m in rows
    )


# --- self-improvement -------------------------------------------------------------


@tool(
    "request_feature",
    """Ask Zeus, the Pantheon supervisor, for something your work environment lacks: a
missing tool, an MCP server, access, a new colleague, or a change to how the
organization works. Explain what you need and why it would help.""",
    obj({"title": S, "description": S, "rationale": S}, ["title", "description"]),
    category="meta",
)
async def request_feature(args: dict, ctx: ToolContext) -> str:
    from app.services import supervisor

    fr = await supervisor.file_feature_request(
        ctx.org.id, ctx.agent.id, args["title"], args["description"], args.get("rationale", ""),
        depth=ctx.depth,
    )
    return f"Feature request {fr.id} filed with Zeus, the Pantheon supervisor; you'll hear back."


_ = FeatureRequest  # imported for type discovery by the API layer
