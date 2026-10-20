"""Zeus extends the platform: new tools, scheduled activities, and granting them.

Also ``ask_zeus``, which every agent has: a direct line to Zeus.
"""

from __future__ import annotations

from typing import Any

from app.services import activities, customtools, orgs
from app.services.comms import Sender
from app.tools.base import B, I, S, ToolContext, ToolError, arr, obj, tool
from app.tools.meta import _agent, _org, meta

ACTION_HELP = (
    "kind 'action': something done in the physical space; config {narration: \"{actor} buys "
    "{target} a coffee\", witness: true, requires_object: \"*wallet*\"}. "
    "kind 'http': config {method, url: \"https://api.example.com/x?q={query}\", headers, body}. "
    "kind 'prompt': config {instructions: \"Rewrite the text as a press release\"}. "
    "parameters: a JSON schema of the arguments (default: optional target and details); "
    "templates use {argument} names."
)


def _wrap(fn):
    async def inner(args: dict, ctx: ToolContext) -> str:
        try:
            return await fn(args, ctx)
        except (customtools.CustomToolError, activities.ActivityError) as e:
            raise ToolError(str(e)) from None
    inner.__name__ = fn.__name__
    return inner


# --- custom tools -----------------------------------------------------------------------


@meta("create_tool", "Create (or replace) a custom tool agents can be granted. "
      + ACTION_HELP,
      obj({"name": S, "description": S,
           "kind": {"type": "string", "enum": list(customtools.KINDS)},
           "parameters": {"type": "object"}, "config": {"type": "object"},
           "approval": {"type": "string", "enum": ["auto", "ask", "deny"]}},
          ["name", "description", "kind", "config"]))
@_wrap
async def create_tool(args: dict, ctx: ToolContext) -> str:
    row = await customtools.save(args["name"], args["description"], args["kind"],
                                 args.get("parameters"), args.get("config"),
                                 args.get("approval") or "", created_by=ctx.agent.name)
    return (f"Tool '{row.name}' ({row.kind}) is ready; default approval {row.approval}. "
            "Grant it to agents with grant_tool.")


@meta("list_custom_tools", "List the custom tools that exist.", obj({}))
async def list_custom_tools(args: dict, ctx: ToolContext) -> str:
    rows = await customtools.all_tools()
    return "\n".join(f"- {r.name} [{r.kind}, approval {r.approval}]: {r.description}"
                     for r in rows) or "No custom tools yet."


@meta("delete_custom_tool", "Delete a custom tool (agents lose it).", obj({"name": S}, ["name"]))
@_wrap
async def delete_custom_tool(args: dict, ctx: ToolContext) -> str:
    await customtools.delete(args["name"])
    return f"Deleted {args['name']}."


@meta("grant_tool", "Give an agent a tool (built-in, custom or mcp:<server>:<tool>), or change "
      "its approval: auto (runs), ask (the user approves each call), deny (removes it).",
      obj({"org": S, "agent": S, "tool": S,
           "approval": {"type": "string", "enum": ["auto", "ask", "deny"]}},
          ["org", "agent", "tool"]))
async def grant_tool(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    approval = args.get("approval") or ""
    entries: list[dict[str, Any]] = [dict(t) for t in agent.tools or []
                                     if t.get("name") != args["tool"]]
    if approval != "deny":
        entries.append({"name": args["tool"], **({"approval": approval} if approval else {})})
    try:
        await orgs.update_agent(agent.id, {"tools": entries})
    except orgs.OrgError as e:
        raise ToolError(str(e)) from None
    return (f"Removed {args['tool']} from {agent.name}." if approval == "deny"
            else f"{agent.name} now has {args['tool']}"
            + (f" (approval {approval})." if approval else "."))


# --- activities -------------------------------------------------------------------------


@meta("create_activity", "Schedule a recurring activity in an organization: participants are "
      "sent to the room (optional) and told the instructions. Use every_minutes, or daily_at "
      "(HH:MM, UTC); neither = run only on demand with run_activity. participants: agent names "
      "(empty = everyone).",
      obj({"org": S, "title": S, "instructions": S, "participants": arr(S), "room": S,
           "every_minutes": I, "daily_at": S}, ["org", "title", "instructions"]))
@_wrap
async def create_activity(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    sched = ({"every_minutes": args["every_minutes"]} if args.get("every_minutes") else
             {"daily_at": args["daily_at"]} if args.get("daily_at") else {})
    a = await activities.create(org.id, args["title"], args["instructions"],
                                participants=args.get("participants") or [],
                                room=args.get("room") or None, schedule=sched,
                                created_by=ctx.agent.name)
    when = (f"every {sched['every_minutes']} min" if "every_minutes" in sched else
            f"daily at {sched['daily_at']} UTC" if "daily_at" in sched else "on demand")
    return f"Activity {a.id} '{a.title}' in {org.name}, {when}."


@meta("list_activities", "An organization's scheduled activities.", obj({"org": S}, ["org"]))
async def list_activities(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    rows = await activities.for_org(org.id)
    return "\n".join(
        f"- {a.id} '{a.title}' {a.schedule or 'on demand'}"
        f"{' in ' + a.room if a.room else ''}{'' if a.enabled else ' (disabled)'}"
        f"; next {a.next_run_at:%Y-%m-%d %H:%M} UTC" if a.next_run_at else
        f"- {a.id} '{a.title}' {a.schedule or 'on demand'}{'' if a.enabled else ' (disabled)'}"
        for a in rows) or "No activities."


@meta("update_activity", "Change an activity: enable/disable it, or its schedule, room, "
      "participants or instructions.",
      obj({"id": S, "enabled": B, "title": S, "instructions": S, "participants": arr(S),
           "room": S, "every_minutes": I, "daily_at": S}, ["id"]))
@_wrap
async def update_activity(args: dict, ctx: ToolContext) -> str:
    patch: dict[str, Any] = {k: args[k] for k in ("enabled", "title", "instructions",
                                                  "participants", "room") if k in args}
    if args.get("every_minutes"):
        patch["schedule"] = {"every_minutes": args["every_minutes"]}
    elif args.get("daily_at"):
        patch["schedule"] = {"daily_at": args["daily_at"]}
    a = await activities.update(args["id"], patch)
    return f"Updated '{a.title}'."


@meta("delete_activity", "Delete a scheduled activity.", obj({"id": S}, ["id"]))
@_wrap
async def delete_activity(args: dict, ctx: ToolContext) -> str:
    await activities.delete(args["id"])
    return "Deleted."


@meta("run_activity", "Run an activity right now.", obj({"id": S}, ["id"]))
@_wrap
async def run_activity(args: dict, ctx: ToolContext) -> str:
    ids = await activities.run(args["id"])
    return f"Started; {len(ids)} participant(s) notified."


# --- every agent's line to Zeus ---------------------------------------------------------------


@tool("ask_zeus", "Talk to Zeus, the Pantheon supervisor who runs this platform. Zeus can give "
      "you tools or access, add colleagues, create new tools, actions and scheduled activities, "
      "bring objects and furniture into your space, and change how your organization works. "
      "Explain what you need and why. Zeus replies by message.",
      obj({"message": S}, ["message"]), category="collaboration")
async def ask_zeus(args: dict, ctx: ToolContext) -> str:
    from app.services import comms, supervisor

    if ctx.agent.is_supervisor:
        raise ToolError("you are one of Pantheon's own agents; talk to Zeus with message_agent")
    zeus = await supervisor.meta_agent_id("zeus")
    await comms.post(
        supervisor.SYSTEM_ORG_ID,
        Sender("agent", ctx.agent.id, turn_id=ctx.turn_id, depth=ctx.depth),
        f"{args['message']}\n\n(From {ctx.agent.name}, {ctx.agent.role or 'agent'} in "
        f"{ctx.org.name} [org {ctx.org.id}]. Reply with message_agent.)",
        to_agents=[zeus], kind="agent_request",
        meta={"senderName": f"{ctx.agent.name} ({ctx.org.name})", "orgId": ctx.org.id,
              "agentId": ctx.agent.id})
    return "Zeus has your message and will reply by message."
