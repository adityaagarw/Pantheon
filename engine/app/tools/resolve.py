"""Resolve an agent's configured tools and execute calls safely."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Agent, Org, Relationship
from app.services import secrets
from app.tools import base
from app.tools.base import ToolContext, ToolError, ToolSpec, truncate
from app.tools.mcp import McpTool, manager

log = logging.getLogger(__name__)

# Import side effect: registers built-in tools.
from app import packs  # noqa: E402  (plugin packs register their tools at startup)
from app.services import flows, life, spatial, workspaces  # noqa: E402
from app.tools import attachments as file_tools  # noqa: E402
from app.tools import (  # noqa: E402,F401
    browser,
    builder,
    collab,
    computer,
    desktop,
    doctools,
    filetools,
    flowtools,
    gittools,
    kronos,
    leadtools,
    lifetools,
    loadout,
    meeting,
    meta,
    oversight,
    packtools,
    scheduling,
    shadowtools,
    skilltools,
    stagetools,
    subagents,
    todos,
    tuningtools,
    web,
    whiteboard,
    workspace,
    world,
    worldadmin,
    zeusbuild,
)


@dataclass
class EffectiveTool:
    name: str
    description: str
    parameters: dict[str, Any]
    approval: str
    spec: ToolSpec | None = None
    mcp: McpTool | None = None
    core: bool = False  # always offered whole (the org's way of working depends on it)

    def openai_schema(self) -> dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}

    @property
    def source(self) -> str:
        return f"mcp:{self.mcp.server_name}" if self.mcp else "builtin"


@dataclass
class ResolvedTools:
    tools: dict[str, EffectiveTool]
    warnings: list[str]


# Tools the org-wide "shell commands without asking" switch covers.
SHELL_TOOLS = ("run_command", "computer_shell")


def _not_for_this_computer(name: str) -> bool:
    """computer_* (screenshot and click) and desktop_* (cua-driver) tools are offered only
    with a computer they work on: the sandbox has both, this computer through cua-driver has
    desktop_*, and the mouse-and-keyboard takeover has computer_*."""
    from app import computer as computers

    backend = computers.backend_id()
    if name.startswith("desktop_"):
        return backend not in ("driver", "cua") or (backend == "cua" and name == "desktop_act")
    if name.startswith("computer_"):
        return backend not in ("native", "cua")
    return False


async def resolve_tools(org: Org, agent: Agent) -> ResolvedTools:
    tools: dict[str, EffectiveTool] = {}
    warnings: list[str] = []
    servers = None
    denied: set[str] = set()
    for entry in agent.tools or []:
        if isinstance(entry, str):
            entry = {"name": entry}
        name = str(entry.get("name", "")).strip()
        approval = entry.get("approval") or ""
        if approval == "deny" or not name:
            denied.add(name)
            continue
        if name.startswith("mcp:"):
            parts = name.split(":", 2)
            if len(parts) != 3:
                warnings.append(f"invalid MCP tool entry '{name}'")
                continue
            _, server_name, pattern = parts
            if servers is None:
                servers = {s.name: s for s in await manager.servers_for_org(org.id)}
            server = servers.get(server_name)
            if server is None:
                warnings.append(f"MCP server '{server_name}' is not configured or is disabled")
                continue
            try:
                mcp_tools = await manager.tools(server.id)
            except Exception as e:  # noqa: BLE001 - a broken server must not stop the agent
                warnings.append(f"MCP server '{server_name}' is unavailable: {e}")
                continue
            for t in mcp_tools:
                if fnmatch.fnmatchcase(t.name, pattern) and t.exposed not in tools:
                    tools[t.exposed] = EffectiveTool(
                        t.exposed, f"[{server_name}] {t.description}", t.parameters,
                        approval or "auto", mcp=t)
            continue
        spec = base.builtin(name)
        if spec is None:
            warnings.append(f"unknown tool '{name}'")
            continue
        if spec.supervisor_only and not agent.is_supervisor:
            continue
        if spec.category.startswith("plugin:") and not packs.is_usable(spec.category[7:]):
            warnings.append(f"tool '{name}' comes from the pack '{spec.category[7:]}', which is "
                            "switched off")
            continue
        effective = approval or spec.default_approval
        if name in SHELL_TOOLS and effective == "ask" and \
                (org.settings or {}).get("shell_approval") == "auto":
            effective = "auto"  # the org runs shell commands without asking (deny still wins)
        tools[name] = EffectiveTool(name, spec.description, spec.parameters, effective, spec=spec)
    # Whoever may draw on whiteboards may also look at them and draw whole diagrams.
    if "whiteboard_draw" in tools:
        for name in ("whiteboard_view", "whiteboard_diagram"):
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters,
                                            tools["whiteboard_draw"].approval, spec=spec)
    # Everyone can keep a calendar (reminders, cron) and read the files they were given.
    for name in (*scheduling.AMBIENT, *file_tools.AMBIENT):
        spec = base.builtin(name)
        if spec is not None and name not in tools and name not in denied:
            tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                        spec=spec)
    # Whoever makes files can reopen them as their reader will.
    if "write_file" in tools or "run_command" in tools:
        for name in filetools.AMBIENT:
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                            spec=spec)
    # Whoever has skills opens them when their trigger fits (services/skills.py).
    if agent.skills and "use_skill" not in denied:
        spec = base.builtin("use_skill")
        if spec is not None:
            tools["use_skill"] = EffectiveTool("use_skill", spec.description, spec.parameters,
                                               "auto", spec=spec, core=True)
    # Everyone can talk to Zeus, the platform supervisor.
    if org.kind != "system" and not agent.is_supervisor and "ask_zeus" not in denied:
        spec = base.builtin("ask_zeus")
        if spec is not None and "ask_zeus" not in tools:
            tools["ask_zeus"] = EffectiveTool("ask_zeus", spec.description, spec.parameters,
                                              "auto", spec=spec)
    # Every working agent keeps its own checklist (todos.py).
    spec = base.builtin("todo")
    if org.kind != "system" and not agent.is_supervisor and spec is not None and \
            "todo" not in denied and "todo" not in tools:
        tools["todo"] = EffectiveTool("todo", spec.description, spec.parameters, "auto", spec=spec)
    # Everyone has a body in a physical space: presence tools come with it.
    if spatial.world_enabled(org) and not agent.is_supervisor:
        for name in world.AMBIENT:
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                            spec=spec, core=True)
    # Leads steer the people they lead. Where the org uses git, everyone has git tools,
    # and leads also review and merge.
    if not agent.is_supervisor:
        async with SessionLocal() as session:
            leads = await session.scalar(select(func.count()).select_from(Relationship).where(
                Relationship.org_id == org.id, Relationship.from_id == agent.id,
                Relationship.kind == "manages"))
            colleagues = await session.scalar(select(func.count()).select_from(Agent).where(
                Agent.org_id == org.id, Agent.id != agent.id, Agent.status != "disabled",
                Agent.is_supervisor.is_(False)))
        # With colleagues, the coordination tools are offered whole (loadout.TEAM_CORE).
        if colleagues:
            for name in loadout.TEAM_CORE:
                if name in tools:
                    tools[name].core = True
        names = list(leadtools.LEAD) if leads else []
        if workspaces.git_settings(org)["enabled"]:
            names += [*gittools.AMBIENT, *(gittools.LEAD if leads else ())]
        for name in names:
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                            spec=spec)
    # Whoever is doing a flow step can finish it.
    if not agent.is_supervisor and "finish_step" not in denied and \
            await flows.running_step_for(agent.id, org.id):
        spec = base.builtin("finish_step")
        if spec is not None:
            tools["finish_step"] = EffectiveTool("finish_step", spec.description,
                                                 spec.parameters, "auto", spec=spec, core=True)
    # In a living world, everyone has needs and wants to look after.
    if life.enabled(org) and (agent.life or {}).get("needs") and not agent.is_supervisor:
        for name in lifetools.AMBIENT:
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                            spec=spec, core=True)
    tools = {k: v for k, v in tools.items() if not _not_for_this_computer(k)}
    # With many tools, most are offered by name only (loadout.py); load_tools brings them in.
    if loadout.lean(org) and len(tools) > loadout.LEAN_ABOVE and "load_tools" not in denied:
        spec = base.builtin("load_tools")
        if spec is not None:
            tools["load_tools"] = EffectiveTool("load_tools", spec.description, spec.parameters,
                                                "auto", spec=spec)
    return ResolvedTools(tools, warnings)


async def execute(tool: EffectiveTool, args: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    """Run a tool; never raises. Returns (output, ok) as text Postgres and models accept."""
    started = time.time()
    out, ok = await _execute(tool, args, ctx)
    if ok and tool.name in AFTER_WRITE:
        out = await _with_file_check(tool.name, args, ctx, out, started)
    out = storable(out)
    masked = secrets.redact(ctx.org.id, out)  # a secret never reaches the model or the record
    if masked != out:
        out = base.ToolOutput(masked, out.images) if isinstance(out, base.ToolOutput) else masked
    return out, ok


AFTER_WRITE = ("write_file", "edit_file", "run_command")


async def _with_file_check(name: str, args: dict[str, Any], ctx: ToolContext, out: Any,
                           started: float) -> Any:
    """A write's result plus what its reader would trip over in the files it produced
    (services/filecheck.py); unchanged when they're fine."""
    from app.services import filecheck

    try:
        note = await asyncio.to_thread(filecheck.after_tool_note, ctx.workdir, name, args, started)
    except Exception:  # noqa: BLE001 - a check never fails the write
        log.debug("file check after %s failed", name, exc_info=True)
        return out
    if not note:
        return out
    return base.ToolOutput(str(out) + note, out.images) if isinstance(out, base.ToolOutput) \
        else str(out) + note


def storable(text: str) -> str:
    """Drop NUL bytes and invalid code points, e.g. from `cat` on a binary file, and say so."""
    nuls = text.count("\x00")
    clean = text.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")
    if clean == text:
        return text
    fixed = sum(a != b for a, b in zip(clean, text.replace("\x00", ""), strict=False))
    parts = [f"{nuls} NUL byte{'s' * (nuls != 1)} removed"] if nuls else []
    if fixed:
        parts.append(f"{fixed} invalid character{'s' * (fixed != 1)} replaced with '?'")
    clean += f"\n[Pantheon: {' and '.join(parts)} so this output could be stored]"
    return base.ToolOutput(clean, text.images) if isinstance(text, base.ToolOutput) else clean


async def _execute(tool: EffectiveTool, args: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    if not isinstance(args, dict):
        return "ERROR: tool arguments must be a JSON object", False
    try:
        if tool.spec is not None:
            tool.spec.validate(args)
            out = await asyncio.wait_for(tool.spec.handler(args, ctx), tool.spec.timeout)
            return (out if isinstance(out, base.ToolOutput) else str(out)), True
        assert tool.mcp is not None
        out, is_error = await manager.call(tool.mcp.server_id, tool.mcp.name, args)
        return (f"ERROR: {out}" if is_error else out), not is_error
    except (ToolError, secrets.SecretError) as e:
        return f"ERROR: {e}", False
    except TimeoutError:
        return f"ERROR: tool '{tool.name}' timed out", False
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 - tool bugs become model-visible errors
        log.exception("tool %s crashed", tool.name)
        return f"ERROR: tool '{tool.name}' failed: {type(e).__name__}: {e}", False


def clip(text: str) -> str:
    return truncate(text)
