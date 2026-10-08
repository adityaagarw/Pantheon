"""Kronos's tools: the user's computer and Pantheon's own code, via Pantheon Host.

Pantheon Host enforces what may be touched; these tools just describe the
operations and format the answers. Code changes happen in a git worktree on a
``kronos/<id>`` branch: start a change, edit its files, check it, then apply it
(the checks run again, and only a passing change is merged and rebuilt).
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Agent
from app.services import comms
from app.services.host import bridge
from app.tools.base import B, I, S, ToolContext, obj, tool, truncate

KRONOS_TOOLS = ["host_info", "host_read", "host_list", "host_search", "host_write", "host_edit",
                "host_run", "code_start", "code_changes", "code_diff", "code_check", "code_apply",
                "code_discard"]


def ktool(name: str, description: str, params: dict, **kw):
    return tool(name, description, params, category="host", supervisor_only=True, **kw)


def _text(result) -> str:
    return truncate(result if isinstance(result, str) else str(result))


@ktool("host_info", "Where you can reach on the user's computer: the Pantheon repo, the "
       "folders the user allowed, the operating system, and the changes in progress.", obj({}))
async def host_info(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("info", {}))


@ktool("host_read", "Read a text file on the user's computer (absolute path, or relative to "
       "the Pantheon repo). Lines are numbered; use offset/limit for big files.",
       obj({"path": S, "offset": I, "limit": I}, ["path"]))
async def host_read(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("read", args))


@ktool("host_list", "List a folder on the user's computer.", obj({"path": S}, ["path"]))
async def host_list(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("list", args))


@ktool("host_search", "Search file contents with a regular expression under a folder (default: "
       "the Pantheon repo). glob narrows the files, e.g. '*.py' or 'web/src/**/*.tsx'.",
       obj({"pattern": S, "path": S, "glob": S}, ["pattern"]))
async def host_search(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("search", args, timeout=180))


@ktool("host_write", "Create or overwrite a file. Inside the Pantheon repo you may only write in "
       "a change's worktree (see code_start); elsewhere only in folders the user allowed.",
       obj({"path": S, "content": S}, ["path", "content"]), side_effects=True)
async def host_write(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("write", args))


@ktool("host_edit", "Replace an exact piece of text in a file (it must match once, unless "
       "replace_all). Same write rules as host_write.",
       obj({"path": S, "old": S, "new": S, "replace_all": B}, ["path", "old", "new"]),
       side_effects=True)
async def host_edit(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("edit", args))


@ktool("host_run", "Run a shell command on the user's computer, in an allowed folder (default: "
       "the Pantheon repo). Prefer code_check for tests.",
       obj({"command": S, "cwd": S, "timeout": I}, ["command"]),
       approval="ask", side_effects=True, timeout=1800)
async def host_run(args: dict, ctx: ToolContext) -> str:
    secs = min(int(args.get("timeout") or 300), 1800)
    return _text(await bridge.call("run", {**args, "timeout": secs}, timeout=secs))


@ktool("code_start", "Start a change to Pantheon's code: makes a git worktree on a new "
       "kronos/<id> branch from the current branch and returns its folder. Edit files there.",
       obj({"title": S}, ["title"]), side_effects=True, timeout=300)
async def code_start(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_start", args, timeout=240))


@ktool("code_changes", "List the code changes in progress.", obj({}))
async def code_changes(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_list", {}))


@ktool("code_diff", "Show a change's diff against where it started.", obj({"change": S}, ["change"]))
async def code_diff(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_diff", args, timeout=60))


@ktool("code_check", "Run the checks for a change: 'quick' is lint and type checks for the "
       "parts it touches; 'full' adds the test suites (can take ~10 minutes).",
       obj({"change": S, "scope": {"type": "string", "enum": ["quick", "full"]}}, ["change"]),
       timeout=2400)
async def code_check(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_check", {"scope": "quick", **args}, timeout=2300))


@ktool("code_apply", "Apply a finished change: commits it, runs the full checks, and only if "
       "they pass merges it into the user's current branch and rebuilds Pantheon (which "
       "restarts it). Write a clear commit message: what changed and why.",
       obj({"change": S, "message": S}, ["change", "message"]), side_effects=True, timeout=2400)
async def code_apply(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_apply", args, timeout=2300))


@ktool("code_discard", "Throw a change away (its worktree and branch).", obj({"change": S}, ["change"]),
       side_effects=True)
async def code_discard(args: dict, ctx: ToolContext) -> str:
    return _text(await bridge.call("change_discard", args, timeout=120))


@tool("ask_kronos", "Ask Kronos, who works on the user's computer, for something that needs "
      "code or the host: a change to Pantheon itself (a new built-in tool, a bug fix, a UI "
      "change) or work with files in folders the user allowed. Describe the outcome you want; "
      "Kronos replies with message_agent when it's done or has a question.",
      obj({"request": S, "context": S}, ["request"]), category="admin", supervisor_only=True)
async def ask_kronos(args: dict, ctx: ToolContext) -> str:
    from app.services.supervisor import SYSTEM_ORG_ID, meta_agent_id

    kronos_id = await meta_agent_id("kronos")
    async with SessionLocal() as session:
        me = await session.get(Agent, ctx.agent.id)
        name = (await session.execute(select(Agent.name).where(Agent.id == kronos_id))).scalar()
    text = (f"Request from {me.name if me else 'an agent'}:\n{args['request']}"
            + (f"\n\nContext: {args['context']}" if args.get("context") else "")
            + f"\n\nWhen you're done (or need a decision), reply with message_agent "
              f"(org: Pantheon, agent: {me.name if me else ctx.agent.id}).")
    await comms.notify(SYSTEM_ORG_ID, [kronos_id], text, depth=ctx.depth + 1,
                       kind="kronos_request", meta={"from": ctx.agent.id})
    if not bridge.connected:
        return (f"Sent to {name or 'Kronos'}. Note: Pantheon Host isn't running on the user's "
                "computer right now, so it can plan but can't touch the computer until the user "
                "starts it (python host/main.py).")
    return f"Sent to {name or 'Kronos'}; it will reply with a message."

