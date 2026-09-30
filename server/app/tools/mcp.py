"""MCP client manager.

Each configured server gets one long-lived connection owned by a dedicated
asyncio task (anyio context managers must be entered and exited in the same
task). Connections start lazily, are restarted with backoff after a crash,
and are restarted when their configuration changes. Every call is bounded by a
timeout, and failures come back to the model as tool errors rather than
crashing the turn.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.security import decrypt_json
from app.events import bus
from app.models import McpServer

log = logging.getLogger(__name__)
CONNECT_TIMEOUT = 60.0


@dataclass
class McpTool:
    server_id: str
    server_name: str
    name: str  # original MCP tool name
    exposed: str  # name given to the model
    description: str
    parameters: dict[str, Any]


def exposed_name(server: str, tool: str) -> str:
    raw = f"mcp__{server}__{tool}"
    clean = re.sub(r"[^a-zA-Z0-9_-]", "_", raw)
    if len(clean) > 64:
        digest = hashlib.sha1(clean.encode()).hexdigest()[:8]
        clean = clean[:55] + "_" + digest
    return clean


def _fingerprint(s: McpServer) -> str:
    return hashlib.sha1(json.dumps(
        [s.transport, s.command, s.args, s.cwd, s.url, s.secrets_enc, s.enabled],
        sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class Connection:
    server_id: str
    name: str
    fingerprint: str
    status: str = "stopped"  # starting | ready | error | stopped
    error: str | None = None
    tools: list[McpTool] = field(default_factory=list)
    session: Any = None
    task: asyncio.Task | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    failures: int = 0
    next_attempt: float = 0.0
    stderr_path: str | None = None


class McpManager:
    def __init__(self) -> None:
        self._conns: dict[str, Connection] = {}
        self._lock = asyncio.Lock()

    # --- lifecycle ------------------------------------------------------------

    async def _load(self, server_id: str) -> McpServer | None:
        async with SessionLocal() as session:
            return await session.get(McpServer, server_id)

    async def ensure(self, server_id: str) -> Connection:
        server = await self._load(server_id)
        if server is None:
            raise RuntimeError(f"MCP server '{server_id}' not found")
        if not server.enabled:
            raise RuntimeError(f"MCP server '{server.name}' is disabled")
        fp = _fingerprint(server)
        async with self._lock:
            conn = self._conns.get(server_id)
            if conn is not None and conn.fingerprint != fp:
                await self._stop(conn)
                conn = None
            if conn is not None and conn.status == "error" and time.monotonic() >= conn.next_attempt:
                await self._stop(conn)
                failures = conn.failures
                conn = None
            else:
                failures = conn.failures if conn else 0
            if conn is None or conn.status == "stopped":
                conn = Connection(server_id, server.name, fp, failures=failures)
                self._conns[server_id] = conn
                conn.status = "starting"
                conn.task = asyncio.create_task(self._run(conn, server), name=f"mcp:{server.name}")
        if conn.status == "error":
            raise RuntimeError(f"MCP server '{server.name}' is unavailable: {conn.error}")
        try:
            await asyncio.wait_for(conn.ready.wait(), CONNECT_TIMEOUT)
        except TimeoutError:
            raise RuntimeError(f"MCP server '{server.name}' did not start within "
                               f"{CONNECT_TIMEOUT:.0f}s") from None
        if conn.status != "ready":
            raise RuntimeError(f"MCP server '{server.name}' failed to start: {conn.error}")
        return conn

    async def _run(self, conn: Connection, server: McpServer) -> None:
        from mcp import ClientSession

        secrets = decrypt_json(server.secrets_enc)
        try:
            async with AsyncExitStack() as stack:
                if server.transport == "stdio":
                    from mcp.client.stdio import StdioServerParameters, stdio_client

                    if not server.command:
                        raise RuntimeError("stdio server needs a command")
                    log_dir = settings.data_path / "logs" / "mcp"
                    log_dir.mkdir(parents=True, exist_ok=True)
                    conn.stderr_path = str(log_dir / f"{server.name}.log")
                    errlog = stack.enter_context(open(conn.stderr_path, "a", encoding="utf-8"))
                    params = StdioServerParameters(
                        command=server.command, args=list(server.args or []),
                        env={**{k: str(v) for k, v in (secrets.get("env") or {}).items()}},
                        cwd=server.cwd or None,
                    )
                    read, write = await stack.enter_async_context(stdio_client(params, errlog))
                elif server.transport == "http":
                    from mcp.client.streamable_http import streamable_http_client
                    from mcp.shared._httpx_utils import create_mcp_http_client

                    if not server.url:
                        raise RuntimeError("http server needs a url")
                    client = await stack.enter_async_context(
                        create_mcp_http_client(headers=secrets.get("headers") or None)
                    )
                    read, write = await stack.enter_async_context(
                        streamable_http_client(server.url, http_client=client)
                    )
                else:
                    raise RuntimeError(f"unknown transport '{server.transport}'")
                session = await stack.enter_async_context(ClientSession(read, write))
                await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT)
                conn.session = session
                conn.tools = await self._list_tools(conn, session)
                conn.status, conn.error, conn.failures = "ready", None, 0
                conn.ready.set()
                await bus.publish("mcp.status", {"serverId": conn.server_id, "name": conn.name,
                                                 "status": "ready", "tools": len(conn.tools)})
                await conn.stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - includes anyio exception groups
            conn.failures += 1
            conn.status = "error"
            conn.error = _describe(e)
            conn.next_attempt = time.monotonic() + min(300.0, 2.0 ** conn.failures)
            log.warning("MCP server %s failed: %s", conn.name, conn.error)
            await bus.publish("mcp.status", {"serverId": conn.server_id, "name": conn.name,
                                             "status": "error", "error": conn.error})
        finally:
            conn.session = None
            if conn.status != "error":
                conn.status = "stopped"
            conn.ready.set()

    async def _list_tools(self, conn: Connection, session: Any) -> list[McpTool]:
        out: list[McpTool] = []
        cursor = None
        for _ in range(50):
            from mcp import types

            result = await asyncio.wait_for(
                session.list_tools(params=types.PaginatedRequestParams(cursor=cursor)
                                   if cursor else None), CONNECT_TIMEOUT)
            for t in result.tools:
                schema = dict(t.input_schema if hasattr(t, "input_schema") else t.inputSchema)
                schema.setdefault("type", "object")
                schema.setdefault("properties", {})
                out.append(McpTool(conn.server_id, conn.name, t.name,
                                   exposed_name(conn.name, t.name),
                                   (t.description or f"{t.name} (MCP {conn.name})")[:2000],
                                   schema))
            cursor = getattr(result, "next_cursor", None) or getattr(result, "nextCursor", None)
            if not cursor:
                break
        return out

    async def _stop(self, conn: Connection) -> None:
        conn.stop.set()
        if conn.task is not None and not conn.task.done():
            try:
                await asyncio.wait_for(asyncio.shield(conn.task), 10)
            except (TimeoutError, Exception):  # noqa: BLE001
                conn.task.cancel()
        conn.status = "stopped"

    async def restart(self, server_id: str) -> None:
        async with self._lock:
            conn = self._conns.pop(server_id, None)
            if conn is not None:
                await self._stop(conn)

    async def stop_all(self) -> None:
        async with self._lock:
            for conn in list(self._conns.values()):
                await self._stop(conn)
            self._conns.clear()

    # --- use ----------------------------------------------------------------------

    async def tools(self, server_id: str) -> list[McpTool]:
        return list((await self.ensure(server_id)).tools)

    async def call(self, server_id: str, tool: str, args: dict[str, Any],
                   timeout: float | None = None) -> tuple[str, bool]:
        conn = await self.ensure(server_id)
        session = conn.session
        if session is None:
            raise RuntimeError(f"MCP server '{conn.name}' is not connected")
        try:
            result = await asyncio.wait_for(
                session.call_tool(tool, args), timeout or settings.mcp_call_timeout_seconds
            )
        except TimeoutError:
            return (f"MCP tool {tool} timed out", True)
        return _render(result), bool(getattr(result, "is_error", getattr(result, "isError", False)))

    def status(self) -> dict[str, dict[str, Any]]:
        return {
            sid: {"name": c.name, "status": c.status, "error": c.error, "tools": len(c.tools),
                  "stderr": c.stderr_path}
            for sid, c in self._conns.items()
        }

    async def servers_for_org(self, org_id: str) -> list[McpServer]:
        async with SessionLocal() as session:
            return list((await session.execute(select(McpServer).where(
                or_(McpServer.org_id == org_id, McpServer.org_id.is_(None)),
                McpServer.enabled.is_(True)))).scalars().all())


def _describe(e: BaseException) -> str:
    if isinstance(e, BaseExceptionGroup):
        return "; ".join(_describe(x) for x in e.exceptions)
    return f"{type(e).__name__}: {e}"


def _render(result: Any) -> str:
    parts: list[str] = []
    for item in getattr(result, "content", None) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(item.text)
        elif kind == "image":
            parts.append(f"[image {getattr(item, 'mime_type', getattr(item, 'mimeType', ''))}]")
        elif kind == "resource":
            res = getattr(item, "resource", None)
            text = getattr(res, "text", None)
            parts.append(text if text else f"[resource {getattr(res, 'uri', '')}]")
        elif kind == "resource_link":
            parts.append(f"[resource link {getattr(item, 'uri', '')}]")
        else:
            parts.append(str(item))
    structured = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None)
    if structured and not parts:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return "\n".join(parts) or "(no output)"


manager = McpManager()
_ = os  # stdio env inherits the process env via the SDK's defaults
