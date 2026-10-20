"""Workspace pages at real addresses, for checking them as their user meets them.

A Stage page is one document: a workspace page checked there lost its relative links, its
shared stylesheets and scripts, and the pages it leads to, so agents started their own web
servers and wrote probe pages to follow a booking from one screen to the next. This serves
workspace folders as plain files on 127.0.0.1, on a port of its own: a separate origin from
Pantheon's interface and API (which only answer the interface's origins), where links lead
to the next page and what one page saves in localStorage the next one reads.

Only folders registered here are served, each under a random token; nothing else is.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import socket
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

log = logging.getLogger(__name__)
HIDDEN = {".git", "node_modules", ".venv", "__pycache__"}
_roots: dict[str, Path] = {}  # token -> folder
_tokens: dict[Path, str] = {}
_live: dict = {"server": None, "task": None, "port": 0}
_lock = asyncio.Lock()


async def _file(request: Request) -> Response:
    root = _roots.get(request.path_params["token"])
    if root is None:
        return PlainTextResponse("not found", status_code=404)
    rel = request.path_params.get("path") or ""
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or any(part in HIDDEN for part in Path(rel).parts):
        return PlainTextResponse("not found", status_code=404)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(target, headers={"Cache-Control": "no-store"})


app = Starlette(routes=[Route("/{token}/{path:path}", _file), Route("/{token}", _file)])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _ensure() -> int:
    async with _lock:
        server, task = _live["server"], _live["task"]
        if server is not None and task is not None and not task.done() and server.started:
            return _live["port"]
        port = _free_port()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                lifespan="off")
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # the main server handles signals
        task = asyncio.create_task(server.serve(), name="workspace-preview")
        for _ in range(50):
            await asyncio.sleep(0.05)
            if server.started or task.done():
                break
        if not server.started:
            raise RuntimeError("couldn't start the workspace preview")
        _live.update(server=server, task=task, port=port)
        return port


async def url_for(folder: Path, rel: str) -> str:
    """The address of `rel` (a path inside `folder`, may end in ?query or #fragment)."""
    folder = folder.resolve()
    token = _tokens.get(folder)
    if token is None:
        token = secrets.token_urlsafe(12)
        _tokens[folder], _roots[token] = token, folder
    port = await _ensure()
    return f"http://127.0.0.1:{port}/{token}/{rel.replace(chr(92), '/').lstrip('/')}"


def serves(url: str) -> bool:
    """Whether `url` is one of the folders served here (the browser may open those)."""
    port = _live["port"]
    prefix = f"http://127.0.0.1:{port}/"
    if not port or not url.startswith(prefix):
        return False
    return url[len(prefix):].split("/", 1)[0].split("?")[0] in _roots


async def stop() -> None:
    server, task = _live["server"], _live["task"]
    _live.update(server=None, task=None, port=0)
    if server is not None:
        server.should_exit = True
    if task is not None:
        try:
            await asyncio.wait_for(task, 5)
        except (TimeoutError, asyncio.CancelledError):
            task.cancel()
