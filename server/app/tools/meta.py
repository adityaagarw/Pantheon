"""Supervisor-only admin tools: build and operate organizations."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.core.security import encrypt_json
from app.models import Agent, FeatureRequest, McpServer, Org, Provider, Task, Turn
from app.services import comms, orgs, tasks
from app.services.comms import Sender
from app.services.orgs import OrgError
from app.tools.base import B, S, ToolContext, ToolError, arr, obj, tool

MODEL_SCHEMA = {"type": "object", "properties": {
    "provider_id": S, "model": S, "temperature": {"type": "number"},
    "max_tokens": {"type": "integer"}, "context_window": {"type": "integer"},
    "reasoning_effort": {"type": "string", "enum": ["off", "low", "medium", "high", "xhigh"],
                         "description": "how much the model thinks; off = no thinking"},
}, "additionalProperties": False}
PERMISSIONS_SCHEMA = {"type": "object", "properties": {"tasks": {
    "type": "object", "additionalProperties": False, "properties": {
        "create": {"type": "boolean"},
        "assign": {"type": "string", "enum": ["anyone", "reports", "self"]},
        "edit": {"type": "string", "enum": ["any", "involved", "assigned"]},
        "require_review": {"type": "boolean"}}}},
    "additionalProperties": False,
    "description": "Task permissions: create tasks?; assign to anyone / reports / self; edit any / "
                   "involved / assigned tasks; require review before closing own tasks"}
TOOL_ENTRY = {"type": "object", "properties": {
    "name": S, "approval": {"type": "string", "enum": ["auto", "ask", "deny"]}},
    "required": ["name"], "additionalProperties": False}


async def _org(ref: str) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, ref)
        if org is None:
            org = (await session.execute(select(Org).where(
                func.lower(Org.name) == ref.strip().lower()))).scalars().first()
    if org is None:
        raise ToolError(f"no organization '{ref}' (use list_orgs)")
    return org


async def _agent(org: Org, ref: str) -> Agent:
    async with SessionLocal() as session:
        a = await comms.resolve_agent(session, org.id, ref)
    if a is None:
        raise ToolError(f"no agent '{ref}' in {org.name}")
    return a


def _dump(value: Any) -> str:
    return json.dumps(value, indent=1, ensure_ascii=False, default=str)


def _wrap(fn):
    async def inner(args: dict, ctx: ToolContext) -> str:
        try:
            return await fn(args, ctx)
        except OrgError as e:
            raise ToolError(str(e)) from None
    inner.__name__ = fn.__name__
    return inner


def meta(name: str, description: str, params: dict[str, Any], **kw: Any):
    def deco(fn):
        return tool(name, description, params, category="admin", supervisor_only=True, **kw)(
            _wrap(fn))
    return deco


@meta("list_orgs", "List all organizations with agent counts and status.", obj({}))
async def list_orgs(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Org, func.count(Agent.id)).outerjoin(
            Agent, Agent.org_id == Org.id).where(Org.kind == "user").group_by(Org.id))).all()
    if not rows:
        return "No organizations yet."
    return "\n".join(f"- {o.name} [{o.id}] status={o.status}, {n} agents — {o.description[:120]}"
                     for o, n in rows)


@meta("get_org", "An organization at a glance: settings, the full agent roster (id, name, "
      "role, team, status, model, tool names), relationships, channels and boards, by name. "
      "Pass `agent` (name or id) for one agent's full definition: persona, tools with "
      "approvals, model, permissions, limits. The 3D office design is left out (see the "
      "world tools).", obj({"org": S, "agent": S}, ["org"]))
async def get_org(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    snap = await orgs.snapshot(org.id)
    if args.get("agent"):
        ref = str(args["agent"]).strip().lower()
        hit = next((a for a in snap["agents"] if ref in (a["id"].lower(), a["name"].lower())), None)
        if hit is None:
            raise ToolError(f"no agent '{args['agent']}' in {org.name}; agents: "
                            + ", ".join(a["name"] for a in snap["agents"]))
        return _dump({k: v for k, v in hit.items() if k not in ("avatar", "orgId")})
    return json.dumps(org_overview(snap), ensure_ascii=False, separators=(",", ":"), default=str)


def org_overview(snap: dict) -> dict:
    """Roster first and compact, so it never gets cut off (issue #4)."""
    names = {a["id"]: a["name"] for a in snap["agents"]}
    workers = [a for a in snap["agents"] if not a["isSupervisor"]]
    tool_sets = [{t["name"] for t in a["tools"]} for a in workers]
    common = set.intersection(*tool_sets) if tool_sets else set()
    o = snap["org"]
    return {
        "org": {"id": o["id"], "name": o["name"], "description": o["description"],
                "status": o["status"], "workspace": o["workspace"], "settings": {
                    k: v for k, v in o["settings"].items()
                    if k != "former_agents"}},
        "agents": [
            {"id": a["id"], "name": a["name"], "role": a["role"], "team": a["team"],
             "status": a["status"], "runtime": a["runtimeStatus"],
             "model": (a["model"] or {}).get("model") or "org default",
             "extraTools": sorted({t["name"] for t in a["tools"]} - common),
             **({"builtIn": a["metaRole"] or True} if a["isSupervisor"] else {})}
            for a in snap["agents"]],
        "toolsEveryAgentHas": sorted(common),
        "relationships": [f"{names.get(r['fromId'], r['fromId'])} {r['kind']} "
                          f"{names.get(r['toId'], r['toId'])}" + (f" ({r['label']})" if r["label"]
                                                                  else "")
                          for r in snap["relationships"]],
        "channels": [{"key": c["key"], "topic": c["topic"], "archived": c["archived"],
                      "members": [names.get(m, "you (the user)" if m == "user" else m)
                                  for m in c["members"]]} for c in snap["channels"]],
        "boards": [{"id": b["id"], "name": b["name"]} for b in snap["boards"]],
        "mcpServers": [m["name"] for m in snap["mcpServers"]],
    }


@meta("create_org", "Create a new organization (with a Main board and #general).",
      obj({"name": S, "description": S, "workspace": {"type": "string",
          "description": "Host folder the agents work in (inside the allowed roots)"},
           "comm_policy": {"type": "string", "enum": ["open", "structured"]}}, ["name"]))
async def create_org(args: dict, ctx: ToolContext) -> str:
    settings = {"comm_policy": args["comm_policy"]} if args.get("comm_policy") else None
    org = await orgs.create_org(args["name"], args.get("description", ""),
                                workspace=args.get("workspace"), settings_=settings)
    return f"Created organization {org.name} [{org.id}]."


@meta("update_org", "Update an organization's name, description, workspace, status "
      "(running/paused) or settings (comm_policy, max_chain_depth, daily_token_budget, "
      "default_model).",
      obj({"org": S, "name": S, "description": S, "workspace": S,
           "status": {"type": "string", "enum": ["running", "paused"]},
           "settings": {"type": "object"}}, ["org"]))
async def update_org(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    patch = {k: v for k, v in args.items() if k != "org"}
    org = await orgs.update_org(org.id, patch)
    return f"Updated {org.name}."


@meta("create_agent", "Hire an agent into an organization. `persona` is its system prompt. "
      "`tools` defaults to a worker toolset; list tool names (see tool_catalog) with an "
      "approval policy. `manager` sets a reporting line.",
      obj({"org": S, "name": S, "role": S, "team": S, "persona": S,
           "tools": arr(TOOL_ENTRY), "model": MODEL_SCHEMA, "worktree": S, "manager": S,
           "permissions": PERMISSIONS_SCHEMA},
          ["org", "name", "role", "persona"]))
async def create_agent(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    data = {k: v for k, v in args.items() if k not in ("org", "manager")}
    agent = await orgs.create_agent(org.id, data)
    if args.get("manager"):
        await orgs.set_relationship(org.id, args["manager"], agent.name, "manages")
    restored = getattr(agent, "restored_channels", [])
    rels = getattr(agent, "restored_relationships", 0)
    back = []
    if restored:
        back.append(f"its predecessor's channels ({', '.join(restored)})")
    if rels:
        back.append(f"{rels} reporting line{'s' * (rels != 1)}")
    note = f" Took over {' and '.join(back)}." if back else ""
    return f"Hired {agent.name} ({agent.role}) into {org.name} [{agent.id}].{note}"


@meta("update_agent", "Change an agent's name, role, team, persona, tools, model, worktree, "
      "limits or status (active/paused/disabled). `tools` replaces the whole list.",
      obj({"org": S, "agent": S, "name": S, "role": S, "team": S, "persona": S,
           "tools": arr(TOOL_ENTRY), "model": MODEL_SCHEMA, "worktree": S,
           "limits": {"type": "object"}, "permissions": PERMISSIONS_SCHEMA,
           "status": {"type": "string", "enum": ["active", "paused", "disabled"]}},
          ["org", "agent"]))
async def update_agent(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    patch = {k: v for k, v in args.items() if k not in ("org", "agent")}
    agent = await orgs.update_agent(agent.id, patch)
    return f"Updated {agent.name}: {', '.join(patch) or 'nothing'}."


@meta("delete_agent", "Remove an agent from an organization (its open tasks return to the "
      "backlog).", obj({"org": S, "agent": S}, ["org", "agent"]))
async def delete_agent(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    await orgs.delete_agent(agent.id)
    return f"Removed {agent.name} from {org.name}."


@meta("set_relationship", "Create a structural relationship: kind=manages (from manages to), "
      "peer, advises, or custom with a label.",
      obj({"org": S, "from": S, "to": S,
           "kind": {"type": "string", "enum": ["manages", "peer", "advises", "custom"]},
           "label": S}, ["org", "from", "to", "kind"]))
async def set_relationship(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    rel = await orgs.set_relationship(org.id, args["from"], args["to"], args["kind"],
                                      args.get("label", ""))
    return f"Relationship set ({rel.kind})."


@meta("remove_relationship", "Remove relationships between two agents (optionally one kind).",
      obj({"org": S, "from": S, "to": S, "kind": S}, ["org", "from", "to"]))
async def remove_relationship(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    a, b = await _agent(org, args["from"]), await _agent(org, args["to"])
    snap = await orgs.snapshot(org.id)
    removed = 0
    for r in snap["relationships"]:
        if r["fromId"] == a.id and r["toId"] == b.id and args.get("kind") in (None, r["kind"]):
            await orgs.remove_relationship(org.id, r["id"])
            removed += 1
    return f"Removed {removed} relationship(s)."


@meta("create_org_channel", "Create a channel in an organization.",
      obj({"org": S, "name": S, "members": arr(S), "topic": S,
           "notify": {"type": "string", "enum": ["all", "mentions"]}},
          ["org", "name", "members"]))
async def create_org_channel(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    try:
        ch = await comms.create_channel(org.id, args["name"], args["members"],
                                        topic=args.get("topic", ""), created_by="user",
                                        notify=args.get("notify", "all"))
    except comms.CommsError as e:
        raise ToolError(str(e)) from None
    return f"Created {ch.key} in {org.name}."


@meta("update_org_channel", "Change a channel in an organization: add or remove members, "
      "rename, set the topic or notifications, or archive/unarchive it (archiving frees the "
      "name). Use it to repair memberships, e.g. after an agent was recreated.",
      obj({"org": S, "channel": S, "add_members": arr(S), "remove_members": arr(S),
           "rename_to": S, "topic": S, "notify": {"type": "string", "enum": ["all", "mentions"]},
           "archive": B}, ["org", "channel"]))
async def update_org_channel(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    try:
        ch, changes = await comms.update_channel(
            org.id, args["channel"], add=args.get("add_members"),
            remove=args.get("remove_members"), name=args.get("rename_to"),
            topic=args.get("topic"), notify=args.get("notify"), archived=args.get("archive"))
    except comms.CommsError as e:
        raise ToolError(str(e)) from None
    return f"{ch.key} in {org.name}: {'; '.join(changes) or 'nothing to change'}."


@meta("list_org_secrets", "The secrets of an organization: names, descriptions, who may "
      "use them and where. Values are never shown; the user adds and edits secrets in the "
      "org's Settings → Secrets.", obj({"org": S}, ["org"]))
async def list_org_secrets(args: dict, ctx: ToolContext) -> str:
    from app.services import secrets

    org = await _org(args["org"])
    rows = await secrets.list_secrets(org.id)
    if not rows:
        return f"{org.name} has no secrets. The user adds them in Settings → Secrets."
    return "\n".join(
        f"{r['name']}{'' if r['enabled'] else ' (disabled)'}: {r['description'] or '-'} | "
        f"agents: {', '.join(r['agents']) or 'none'} | hosts: {', '.join(r['domains']) or 'none'}"
        f" | shell: {'yes' if r['allowShell'] else 'no'}" for r in rows)


@meta("grant_secret", "Let an agent use an existing secret (or stop it with `revoke`: true). "
      "You can't create secrets or see values: ask the user to add one in Settings → "
      "Secrets. Never ask anyone to paste a credential into chat.",
      obj({"org": S, "secret": S, "agent": S, "revoke": B}, ["org", "secret", "agent"]))
async def grant_secret(args: dict, ctx: ToolContext) -> str:
    from app.services import secrets

    org = await _org(args["org"])
    try:
        return await secrets.set_grant(org.id, args["secret"], args["agent"],
                                       granted=not args.get("revoke"))
    except secrets.SecretError as e:
        raise ToolError(str(e)) from None


@meta("meetings_in_progress", "Meetings an organization currently has in progress (who, "
      "where, since when). Use end_meeting on one that's stuck.", obj({"org": S}, ["org"]))
async def meetings_in_progress(args: dict, ctx: ToolContext) -> str:
    from app.services import meetings

    org = await _org(args["org"])
    rows = await meetings.running_meetings(org.id)
    if not rows:
        return f"No meetings in progress in {org.name}."
    snap = await orgs.snapshot(org.id)
    names = {a["id"]: a["name"] for a in snap["agents"]}
    return "\n".join(
        f"{m.id}: {m.agenda.splitlines()[0][:80]} | in {(m.options or {}).get('room') or 'the meeting room'}"
        f" | since {m.created_at:%Y-%m-%d %H:%M} UTC | "
        f"{', '.join(names.get(p, p) for p in m.participants or [])}" for m in rows)


@meta("end_meeting", "End a meeting that's stuck in progress, freeing its room and participants. "
      "Get its id from meetings_in_progress.", obj({"meeting": S, "reason": S}, ["meeting"]))
async def end_meeting(args: dict, ctx: ToolContext) -> str:
    from app.services import meetings

    row = await meetings.end_meeting(args["meeting"], args.get("reason") or "ended by Zeus")
    if row is None:
        raise ToolError(f"no meeting in progress with id {args['meeting']}")
    return f"Ended meeting {row.id}; its room and participants are free."


@meta("tool_catalog", "All built-in tools (with categories and default approval) and every "
      "configured MCP server with its tools.", obj({"org": S}))
async def tool_catalog(args: dict, ctx: ToolContext) -> str:
    from app.tools import base
    from app.tools.mcp import manager

    lines = ["Built-in tools:"]
    for s in base.builtin_catalog():
        if s.supervisor_only:
            continue
        lines.append(f"- {s.name} [{s.category}, default approval {s.default_approval}]: "
                     f"{s.description.splitlines()[0]}")
    org_id = (await _org(args["org"])).id if args.get("org") else None
    async with SessionLocal() as session:
        stmt = select(McpServer)
        if org_id:
            stmt = stmt.where((McpServer.org_id == org_id) | McpServer.org_id.is_(None))
        servers = (await session.execute(stmt)).scalars().all()
    lines.append("\nMCP servers (grant with tool entries like mcp:<server>:* or "
                 "mcp:<server>:<tool>):")
    for srv in servers:
        scope = "global" if srv.org_id is None else f"org {srv.org_id}"
        try:
            tl = await manager.tools(srv.id) if srv.enabled else []
            names = ", ".join(t.name for t in tl) or "(none)"
            lines.append(f"- {srv.name} ({scope}, {srv.transport}): {names}")
        except Exception as e:  # noqa: BLE001
            lines.append(f"- {srv.name} ({scope}): UNAVAILABLE — {e}")
    if not servers:
        lines.append("- (none configured)")
    return "\n".join(lines)


@meta("list_models", "Configured LLM providers and their models (use provider_id + model in "
      "an agent's model settings).", obj({}))
async def list_models(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Provider))).scalars().all()
    if not rows:
        return "No providers configured."
    return "\n".join(
        f"- {p.name} [{p.id}] type={p.type}{' (default)' if p.is_default else ''}: "
        + ", ".join(str(m.get("id")) for m in p.models or [] if isinstance(m, dict))
        for p in rows)


@meta("add_mcp_server", "Register an MCP server. stdio: command + args (+ env). http: url "
      "(+ headers). Omit org to make it available to every organization.",
      obj({"org": S, "name": {"type": "string", "pattern": "^[a-z0-9_-]{1,40}$"},
           "description": S, "transport": {"type": "string", "enum": ["stdio", "http"]},
           "command": S, "args": arr(S), "cwd": S, "url": S,
           "env": {"type": "object", "additionalProperties": {"type": "string"}},
           "headers": {"type": "object", "additionalProperties": {"type": "string"}}},
          ["name", "transport"]),
      approval="ask")
async def add_mcp_server(args: dict, ctx: ToolContext) -> str:
    org_id = (await _org(args["org"])).id if args.get("org") else None
    async with SessionLocal() as session:
        srv = McpServer(org_id=org_id, name=args["name"], description=args.get("description", ""),
                        transport=args["transport"], command=args.get("command"),
                        args=args.get("args") or [], cwd=args.get("cwd"), url=args.get("url"),
                        secrets_enc=encrypt_json({"env": args.get("env") or {},
                                                  "headers": args.get("headers") or {}}))
        session.add(srv)
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            raise ToolError(f"an MCP server named '{args['name']}' already exists") from None
    return f"Registered MCP server {srv.name} [{srv.id}]. Run test_mcp_server to verify it."


@meta("update_mcp_server", "Enable/disable or change an MCP server's command/url.",
      obj({"server": S, "enabled": B, "command": S, "args": arr(S), "url": S, "description": S},
          ["server"]), approval="ask")
async def update_mcp_server(args: dict, ctx: ToolContext) -> str:
    from app.tools.mcp import manager

    async with SessionLocal() as session:
        srv = await session.get(McpServer, args["server"]) or (await session.execute(
            select(McpServer).where(McpServer.name == args["server"]))).scalars().first()
        if srv is None:
            raise ToolError(f"no MCP server '{args['server']}'")
        for k in ("enabled", "command", "args", "url", "description"):
            if k in args:
                setattr(srv, k, args[k])
        await session.commit()
    await manager.restart(srv.id)
    return f"Updated MCP server {srv.name}."


@meta("test_mcp_server", "Connect to an MCP server and list its tools.", obj({"server": S},
                                                                               ["server"]))
async def test_mcp_server(args: dict, ctx: ToolContext) -> str:
    from app.tools.mcp import manager

    async with SessionLocal() as session:
        srv = await session.get(McpServer, args["server"]) or (await session.execute(
            select(McpServer).where(McpServer.name == args["server"]))).scalars().first()
    if srv is None:
        raise ToolError(f"no MCP server '{args['server']}'")
    await manager.restart(srv.id)
    try:
        tl = await manager.tools(srv.id)
    except Exception as e:  # noqa: BLE001
        raise ToolError(f"connection failed: {e}") from None
    return f"{srv.name} is working. Tools: " + ", ".join(t.name for t in tl)


@meta("list_feature_requests", "Feature requests filed by agents.",
      obj({"status": S, "org": S}))
async def list_feature_requests(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        stmt = select(FeatureRequest).order_by(FeatureRequest.created_at.desc()).limit(100)
        if args.get("status"):
            stmt = stmt.where(FeatureRequest.status == args["status"])
        if args.get("org"):
            stmt = stmt.where(FeatureRequest.org_id == (await _org(args["org"])).id)
        rows = (await session.execute(stmt)).scalars().all()
    if not rows:
        return "No feature requests."
    return "\n".join(f"- {r.id} [{r.status}] org={r.org_id} from={r.requester_id}: {r.title}"
                     + (f" — resolution: {r.resolution}" if r.resolution else "") for r in rows)


@meta("update_feature_request", "Record a feature request's status and resolution.",
      obj({"id": S, "status": {"type": "string", "enum": ["triaged", "accepted", "in_progress",
                                                          "done", "rejected"]},
           "resolution": S}, ["id", "status"]))
async def update_feature_request(args: dict, ctx: ToolContext) -> str:
    from app.events import bus
    from app.services.supervisor import fr_to_dict

    async with SessionLocal() as session:
        fr = await session.get(FeatureRequest, args["id"])
        if fr is None:
            raise ToolError(f"no feature request {args['id']}")
        fr.status = args["status"]
        if args.get("resolution"):
            fr.resolution = args["resolution"]
        await session.commit()
    await bus.publish("feature_request.updated", fr_to_dict(fr), org_id=fr.org_id)
    return f"{fr.id} is now {fr.status}."


@meta("message_agent", "Send a message to an agent in any organization (e.g. to answer a "
      "feature request or give direction).", obj({"org": S, "agent": S, "content": S},
                                                 ["org", "agent", "content"]))
async def message_agent(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    await comms.post(org.id, Sender("agent", ctx.agent.id, turn_id=ctx.turn_id, depth=ctx.depth),
                     args["content"], to_agents=[agent.id], kind="supervisor",
                     meta={"senderName": f"{ctx.agent.name} (Pantheon)"})
    return f"Message delivered to {agent.name}."


@meta("assign_goal", "Give an organization a goal: creates a task for the chosen agent "
      "(usually its lead), who will plan and delegate it.",
      obj({"org": S, "agent": S, "title": S, "description": S, "acceptance": S},
          ["org", "agent", "title", "description"]))
async def assign_goal(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    t = await tasks.create_task(org.id, Sender.user(), title=args["title"],
                                description=args["description"],
                                acceptance=args.get("acceptance", ""), assignee=agent.id,
                                priority="high")
    return f"Created {tasks.task_ref(t)} for {agent.name} in {org.name}."


@meta("org_activity", "Health report for an organization: agent statuses and errors, recent "
      "turns, token use and task counts by status.", obj({"org": S}, ["org"]))
async def org_activity(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    async with SessionLocal() as session:
        agents = (await session.execute(select(Agent).where(Agent.org_id == org.id))).scalars()
        task_counts = (await session.execute(select(Task.status, func.count()).where(
            Task.org_id == org.id).group_by(Task.status))).all()
        turns = (await session.execute(select(Turn).where(Turn.org_id == org.id).order_by(
            Turn.started_at.desc()).limit(15))).scalars().all()
        agent_rows = list(agents)
        tokens = (await session.execute(select(func.coalesce(func.sum(
            Turn.input_tokens + Turn.output_tokens), 0), func.coalesce(func.sum(Turn.cost_usd), 0))
            .where(Turn.org_id == org.id))).one()
    names = {a.id: a.name for a in agent_rows}
    lines = [f"{org.name} — status {org.status}; total tokens {int(tokens[0]):,}, "
             f"cost ${float(tokens[1]):.4f}", "Agents:"]
    lines += [f"- {a.name}: {a.status}/{a.runtime_status}"
              + (f" — {a.runtime_detail[:200]}" if a.runtime_detail else "") for a in agent_rows]
    lines.append("Tasks: " + (", ".join(f"{s}={n}" for s, n in task_counts) or "none"))
    lines.append("Recent turns:")
    lines += [f"- {names.get(t.agent_id, t.agent_id)} [{t.status}] {t.summary[:160]}"
              + (f" ERROR {t.error[:200]}" if t.error else "") for t in turns]
    return "\n".join(lines)
