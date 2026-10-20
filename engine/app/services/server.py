"""Server mode: Pantheon in a browser, from this computer or others on the network.

The desktop app's engine listens on 127.0.0.1 for its own window. Turned on (Settings →
Server), it also listens on every interface at a chosen port and serves the interface
there (app/core/webui.py); other devices need the access code (app/core/access.py).
``pantheon-serve`` (the installed runtime) or ``python -m app.serve --public`` runs the
engine that way without the window, for a computer used as a server.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from typing import Any

import uvicorn

from app.core import access

log = logging.getLogger(__name__)
KEY = "server"
DEFAULT_PORT = 8730
_state: dict[str, Any] = {"enabled": False, "port": DEFAULT_PORT, "code": ""}
_live: dict[str, Any] = {"server": None, "task": None, "error": None, "watch": None}
# On Windows, asyncio closes a listening socket for good when one accept fails (WinError 64,
# a device dropping mid-connect), and the server goes on looking started: it is checked this
# often and opened again.
WATCH_SECONDS = 15


async def _save() -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
        if row is None:
            session.add(Setting(key=KEY, value=dict(_state)))
        else:
            row.value = dict(_state)
        await session.commit()


async def load() -> None:
    """At startup: the saved choice, an access code (made once), and the server if it's on."""
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
    if row and isinstance(row.value, dict):
        _state.update(row.value)
    if not _state.get("code"):
        _state["code"] = new_code()
        await _save()
    access.set_code(_state["code"])
    if _state.get("enabled"):
        await _start()


def new_code() -> str:
    return "-".join(secrets.token_hex(2) for _ in range(4))


async def configure(enabled: bool | None = None, port: int | None = None,
                    new_access_code: bool = False) -> dict[str, Any]:
    if port is not None:
        if not 1024 <= int(port) <= 65535:
            raise ValueError("the port must be between 1024 and 65535")
        _state["port"] = int(port)
    if enabled is not None:
        _state["enabled"] = bool(enabled)
    if new_access_code:
        _state["code"] = new_code()
        access.set_code(_state["code"])
    await _save()
    if _live["watch"] is not None:
        _live["watch"].cancel()
        _live["watch"] = None
    await stop()
    if _state["enabled"]:
        await _start()
    return status()


async def _start() -> None:
    from app.core.config import settings
    from app.main import app

    _live["error"] = None
    if int(_state["port"]) == settings.port:
        _live["error"] = f"port {settings.port} is the app's own; choose another"
        return
    config = uvicorn.Config(app, host="0.0.0.0", port=int(_state["port"]), log_level="warning",
                            lifespan="off", ws_max_size=64 * 1024 * 1024)
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # the main server handles signals
    _live["server"] = server
    _live["task"] = asyncio.create_task(_serve(server), name="server-mode")
    for _ in range(50):  # until it listens, or fails (e.g. the port is taken)
        await asyncio.sleep(0.1)
        if server.started or _live["task"].done():
            break
    if _live["watch"] is None or _live["watch"].done():
        _live["watch"] = asyncio.create_task(_watch(), name="server-mode-watch")


def listening(server: uvicorn.Server | None) -> bool:
    """Whether the server still has an open listening socket."""
    if server is None or not server.started:
        return False
    return any(s.fileno() != -1 for srv in getattr(server, "servers", []) for s in srv.sockets or [])


async def _watch() -> None:
    while _state["enabled"]:
        await asyncio.sleep(WATCH_SECONDS)
        server = _live["server"]
        if _state["enabled"] and server is not None and server.started and not listening(server):
            log.warning("server mode stopped listening on port %s; opening it again", _state["port"])
            await stop()
            await _start()


async def _serve(server: uvicorn.Server) -> None:
    try:
        await server.serve()
    except (OSError, SystemExit) as e:
        _live["error"] = f"couldn't listen on port {_state['port']}: {e}"
    if not server.started and not _live["error"]:
        _live["error"] = f"couldn't listen on port {_state['port']} (is it in use?)"


async def stop() -> None:
    server, task = _live["server"], _live["task"]
    _live.update(server=None, task=None)
    if server is not None:
        server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(task, 10)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()


def addresses() -> list[str]:
    """This computer's addresses other devices may reach it at."""
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no traffic: picks the interface a route would use
            found.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def status() -> dict[str, Any]:
    server = _live["server"]
    running = listening(server)
    port = int(_state["port"])
    hosts = ["localhost", *addresses()]
    return {"enabled": bool(_state["enabled"]), "port": port, "running": running,
            "error": _live["error"], "accessCode": _state["code"],
            "urls": [f"http://{h}:{port}/" for h in hosts]}
