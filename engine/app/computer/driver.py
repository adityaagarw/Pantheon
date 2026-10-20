"""This computer, through cua-driver (https://github.com/trycua/cua, MIT).

cua-driver drives the user's apps in the background: it reads each window's accessibility
tree, clicks and types into a given app and window without moving the user's mouse or
taking the keyboard focus, and shows each agent's own cursor. The engine keeps one
connection to ``cua-driver mcp`` (its MCP server over stdio, from the bundled ``cua_driver``
package) and offers a chosen set of its tools to agents as ``desktop_*`` tools
(app/tools/desktop.py; their schemas are in driver_tools.json).

Telemetry is off: the driver is started with DO_NOT_TRACK=1.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
TOOLS: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).with_name("driver_tools.json")).read_text(encoding="utf-8"))
CALL_TIMEOUT = 120.0


class DriverError(RuntimeError):
    pass


class Driver:
    """One connection to cua-driver's MCP: this computer's (``cua-driver mcp`` over stdio), or,
    with url and token, a sandbox's (cua-spacesd's /mcp over HTTP, app/computer/sandbox.py)."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url, self.token = url, token
        self.tools: set[str] = set()
        # cua-driver binds a session name to the connection that made it: names are made
        # unique per connection, so a reconnect (or an engine restart) never collides.
        self.nonce = secrets.token_hex(3)
        self._session: Any = None
        self._task: asyncio.Task | None = None
        self._ready: asyncio.Event | None = None
        self._stop: asyncio.Event | None = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    async def _run(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        try:
            async with contextlib.AsyncExitStack() as stack:
                if self.url:
                    from mcp.client.streamable_http import streamable_http_client
                    from mcp.shared._httpx_utils import create_mcp_http_client

                    client = await stack.enter_async_context(create_mcp_http_client(
                        headers={"Authorization": f"Bearer {self.token}"}))
                    streams = await stack.enter_async_context(
                        streamable_http_client(self.url, http_client=client))
                    read, write = streams[0], streams[1]
                else:
                    env = {**os.environ, "DO_NOT_TRACK": "1",
                           "CUA_DRIVER_RS_TELEMETRY_ENABLED": "0"}
                    params = StdioServerParameters(
                        command=sys.executable, args=["-m", "cua_driver", "mcp"], env=env)
                    read, write = await stack.enter_async_context(stdio_client(params))
                session = await stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.tools = {t.name for t in (await session.list_tools()).tools}
                self._session = session
                self._ready.set()
                await self._stop.wait()
        except Exception as e:  # noqa: BLE001 - reported to the agent that needed it
            self._error = f"cua-driver didn't start: {type(e).__name__}: {e}"
            log.warning("%s", self._error)
        finally:
            self._session = None
            self._ready.set()

    async def _ensure(self) -> Any:
        async with self._lock:
            if self._session is not None:
                return self._session
            self._error = None
            self._ready, self._stop = asyncio.Event(), asyncio.Event()
            self._task = asyncio.create_task(self._run(), name="cua-driver")
            try:
                await asyncio.wait_for(self._ready.wait(), 60)
            except TimeoutError:
                raise DriverError("cua-driver took too long to start") from None
            if self._session is None:
                raise DriverError(self._error or "cua-driver didn't start")
            return self._session

    async def call(self, tool: str, args: dict[str, Any]) -> tuple[str, list[str], bool]:
        """Run a driver tool: (text, images as data URLs, is_error)."""
        session = await self._ensure()
        if args.get("session"):
            args = {**args, "session": f"{args['session']}-{self.nonce}"}
        try:
            result = await asyncio.wait_for(session.call_tool(tool, args), CALL_TIMEOUT)
        except TimeoutError:
            raise DriverError(f"{tool} took longer than {int(CALL_TIMEOUT)} s") from None
        except Exception as e:  # noqa: BLE001 - a dead driver is restarted on the next call
            await self.stop()
            raise DriverError(f"cua-driver failed: {type(e).__name__}: {e}") from None
        texts, images = [], []
        for item in result.content or []:
            if item.type == "text":
                texts.append(item.text)
            elif item.type == "image":
                images.append(f"data:{item.mime_type};base64,{item.data}")
        return "\n".join(texts) or "(done)", images, bool(result.is_error)

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, 10)
        self._task = None
        self._session = None

    # --- the computer_* tools (a screenshot of the whole screen; nothing else) -------------

    async def cmd(self, command: str, timeout: float = 60, **params: Any) -> dict[str, Any]:
        from app.computer.cua import ComputerError

        raise ComputerError("on this computer, agents use the desktop_* tools (desktop_windows, "
                            "desktop_look, desktop_act, …), not computer_" + command)

    async def screenshot(self, quality: int = 70) -> str:
        _, images, error = await self.call("get_desktop_state", {"max_image_dimension": 1366})
        if error or not images:
            raise DriverError("couldn't take a screenshot")
        return images[0]

    async def status(self) -> dict[str, Any]:
        try:
            await self._ensure()
            return {"ok": True}
        except DriverError as e:
            return {"ok": False, "error": str(e)}


driver = Driver()
