"""Resolve an agent's configured tools and execute calls safely."""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import dataclass
from typing import Any

from app.models import Agent, Org
from app.tools import base
from app.tools.base import ToolContext, ToolError, ToolSpec, truncate
from app.tools.mcp import McpTool, manager

log = logging.getLogger(__name__)

# Import side effect: registers built-in tools.
from app import plugins  # noqa: E402
from app.services import spatial  # noqa: E402
from app.tools import attachments as file_tools  # noqa: E402
from app.tools import (  # noqa: E402,F401
    browser,
    collab,
    computer,
    meeting,
    meta,
    oversight,
    scheduling,
    stagetools,
    web,
    whiteboard,
    workspace,
    world,
    worldadmin,
    zeusbuild,
)

plugins.load_all()  # plugin tools register on import


@dataclass
class EffectiveTool:
    name: str
    description: str
    parameters: dict[str, Any]
    approval: str
    spec: ToolSpec | None = None
    mcp: McpTool | None = None

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
        tools[name] = EffectiveTool(name, spec.description, spec.parameters,
                                    approval or spec.default_approval, spec=spec)
    # Everyone can keep a calendar (reminders, cron) and read the files they were given.
    for name in (*scheduling.AMBIENT, *file_tools.AMBIENT):
        spec = base.builtin(name)
        if spec is not None and name not in tools and name not in denied:
            tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                        spec=spec)
    # Everyone can talk to Zeus, the platform supervisor.
    if org.kind != "system" and not agent.is_supervisor and "ask_zeus" not in denied:
        spec = base.builtin("ask_zeus")
        if spec is not None and "ask_zeus" not in tools:
            tools["ask_zeus"] = EffectiveTool("ask_zeus", spec.description, spec.parameters,
                                              "auto", spec=spec)
    # Everyone has a body in a physical space: presence tools come with it.
    if spatial.world_enabled(org) and not agent.is_supervisor:
        for name in world.AMBIENT:
            spec = base.builtin(name)
            if spec is not None and name not in tools and name not in denied:
                tools[name] = EffectiveTool(name, spec.description, spec.parameters, "auto",
                                            spec=spec)
    return ResolvedTools(tools, warnings)


async def execute(tool: EffectiveTool, args: dict[str, Any], ctx: ToolContext) -> tuple[str, bool]:
    """Run a tool; never raises. Returns (output, ok) as text Postgres and models accept."""
    out, ok = await _execute(tool, args, ctx)
    return storable(out), ok


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
    except ToolError as e:
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
