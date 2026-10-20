"""Pantheon's tools for an external agent (a CLI like Claude Code or Codex), as a stdio MCP
server the agent's process starts: ``python -m app.mcpbridge``.

It knows nothing itself: it relays the agent's list and calls to the engine
(app/api/bridge.py) with the token of the turn it was started for, given in the
environment (PANTHEON_BRIDGE_URL, PANTHEON_BRIDGE_TOKEN).
"""

from __future__ import annotations

import os
from typing import Any

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

URL = os.environ.get("PANTHEON_BRIDGE_URL", "http://127.0.0.1:8720").rstrip("/")
HEADERS = {"Authorization": f"Bearer {os.environ.get('PANTHEON_BRIDGE_TOKEN', '')}"}
TIMEOUT = httpx.Timeout(300.0, connect=10.0)
# A call can wait for the user's approval, or be a long job: no read limit.
CALL_TIMEOUT = httpx.Timeout(None, connect=10.0)


async def _list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{URL}/api/v1/bridge/tools", headers=HEADERS)
    if r.status_code != 200:
        return types.ListToolsResult(tools=[])
    return types.ListToolsResult(tools=[
        types.Tool(name=t["name"], description=t["description"],
                   input_schema=t.get("parameters") or {"type": "object", "properties": {}})
        for t in r.json()])


async def _call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
    async with httpx.AsyncClient(timeout=CALL_TIMEOUT) as client:
        r = await client.post(f"{URL}/api/v1/bridge/call", headers=HEADERS,
                              json={"name": params.name, "arguments": params.arguments or {}})
    if r.status_code != 200:
        text, ok = f"Pantheon couldn't run {params.name}: {r.text[:500]}", False
    else:
        body = r.json()
        text, ok = str(body.get("text") or ""), bool(body.get("ok"))
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                is_error=not ok)


server = Server("pantheon", version="2.0.0",
                instructions="Pantheon's tools: messages, tasks, the board, memory, flow steps.",
                on_list_tools=_list_tools, on_call_tool=_call_tool)


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(main)
