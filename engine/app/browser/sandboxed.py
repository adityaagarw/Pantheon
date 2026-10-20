"""Agents' browser in the sandbox desktop: a real, visible Chromium.

Sites that turn away the built-in headless browser (bot checks, "unusual traffic") mostly
let a normal browser on a normal desktop through. This one runs on the sandbox desktop
(app/computer/sandbox.py) with a profile that lasts, so:
- the user watches it on the Stage (Sandbox desktop) and can take over, e.g. for a
  human check or to sign in to a site once for the agents;
- the agents use it with the same browser_* tools as the built-in one.

Pantheon reaches its debugging port through ``docker exec … socat``, so the sandbox
publishes no new port. Where agents browse is the ``browser`` setting:
- ``auto`` (the default): the built-in browser; a site that blocks it opens in the sandbox
  instead, when the sandbox is set up;
- ``builtin``: always the built-in browser;
- ``sandbox``: always the sandbox's.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from typing import Any

from app.computer import sandbox

log = logging.getLogger(__name__)
SETTING = "browser"
WHERE = ("auto", "builtin", "sandbox")
PORT = 9222  # Chromium's debugging port, inside the container only
PROFILE = "$HOME/.pantheon-browser"
FIND_CHROME = "ls -d ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | sort -V | tail -1"

_where: dict[str, Any] = {}
_ready: dict[str, Any] = {"at": 0.0, "ok": False}
_lock = asyncio.Lock()
_tunnel: Tunnel | None = None
_moved: set[str] = set()  # agents that auto mode moved to the sandbox


class SandboxBrowserError(RuntimeError):
    pass


# --- the setting ----------------------------------------------------------------------------


async def where() -> str:
    from app.core.db import SessionLocal
    from app.models import Setting

    if "where" not in _where:
        async with SessionLocal() as session:
            row = await session.get(Setting, SETTING)
        value = row.value.get("where") if row and isinstance(row.value, dict) else None
        _where["where"] = value if value in WHERE else "auto"
    return _where["where"]


async def set_where(value: str) -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    if value not in WHERE:
        raise ValueError(f"where must be one of {', '.join(WHERE)}")
    async with SessionLocal() as session:
        row = await session.get(Setting, SETTING)
        if row is None:
            session.add(Setting(key=SETTING, value={"where": value}))
        else:
            row.value = {**(row.value or {}), "where": value}
        await session.commit()
    _where["where"] = value
    _moved.clear()


async def available() -> bool:
    """The sandbox is set up (it starts when needed). Checked at most once a minute."""
    now = time.monotonic()
    if now - _ready["at"] < 60:
        return _ready["ok"]
    ok = False
    try:
        st = await sandbox._load()
        ok = bool(st.get("token")) and sandbox.docker_cli() is not None \
            and await sandbox.docker_running() and await sandbox.container_state() != "none"
    except Exception:  # noqa: BLE001 - no Docker: not available
        ok = False
    _ready.update(at=now, ok=ok)
    return ok


async def where_for(agent_id: str) -> str:
    """'builtin' or 'sandbox': where this agent's browser is now."""
    w = await where()
    if w == "sandbox":
        return "sandbox"
    if w == "auto" and agent_id in _moved:
        return "sandbox"
    return "builtin"


def move(agent_id: str) -> None:
    _moved.add(agent_id)


def forget(agent_id: str) -> None:
    _moved.discard(agent_id)


# --- blocked pages --------------------------------------------------------------------------

TITLE_MARKS = ("just a moment", "attention required", "access denied", "are you a robot",
               "verify you are human", "verifying you are human", "security check",
               "pardon our interruption", "you have been blocked", "request blocked",
               "human verification", "bot verification", "verification required",
               "403 forbidden", "captcha")
TEXT_MARKS = ("verify you are human", "verifying you are human", "checking your browser",
              "checking if the site connection is secure", "are you a robot",
              "not a robot", "unusual traffic", "press & hold", "press and hold",
              "complete the security check", "enable javascript and cookies to continue",
              "sorry, you have been blocked", "access to this page has been denied",
              "please verify you are a human", "solve the captcha", "complete the captcha",
              "why have i been blocked", "automated queries", "detected unusual activity",
              "verification required", "slide right to")


def blocked(status: int | None, title: str, text: str) -> str | None:
    """Why this page looks like a site turning the browser away, or None."""
    t, body = (title or "").lower(), (text or "").lower()[:4000]
    for m in TITLE_MARKS:
        if m in t:
            return f'the page is "{title.strip()[:80]}"'
    for m in TEXT_MARKS:
        if m in body and (len(body) < 2500 or (status or 200) >= 400):
            return f'it says "{m}"'
    if status in (403, 429) and len(body) < 1500:
        return f"it answered HTTP {status}"
    return None


# --- Chromium in the sandbox ----------------------------------------------------------------


async def _sh(command: str, *, user: str = "1000", timeout: float = 60) -> tuple[int, str]:
    return await sandbox._docker("exec", "-u", user, sandbox.CONTAINER, "bash", "-lc", command,
                                 timeout=timeout)


async def _version() -> dict[str, Any] | None:
    code, out = await _sh(
        "python3 -c \"import urllib.request,sys;"
        f"sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:{PORT}/json/version',"
        "timeout=2).read().decode())\"", timeout=20)
    if code != 0:
        return None
    try:
        return json.loads(out[out.index("{"):])
    except ValueError:
        return None


async def _install() -> str:
    """Playwright's Chromium in the sandbox (once, ~170 MB), with the libraries it needs."""
    code, out = await _sh("python3 -m playwright install chromium", timeout=900)
    if code != 0:
        raise SandboxBrowserError("couldn't download Chromium into the sandbox: " + out[-300:])
    await _sh("python3 -m playwright install-deps chromium", user="0", timeout=900)
    code, path = await _sh(FIND_CHROME)
    if code != 0 or not path.strip():
        raise SandboxBrowserError("Chromium didn't install in the sandbox")
    return path.strip().splitlines()[-1]


async def _start_chrome() -> None:
    code, path = await _sh(FIND_CHROME)
    chrome = path.strip().splitlines()[-1] if code == 0 and path.strip() else await _install()
    flags = (f"--remote-debugging-port={PORT} --user-data-dir={PROFILE} --no-first-run "
             "--no-default-browser-check --start-maximized --disable-dev-shm-usage "
             "--password-store=basic --disable-features=Translate")
    code, out = await sandbox._docker(
        "exec", "-d", "-u", "1000", "-e", "DISPLAY=:1", sandbox.CONTAINER, "bash", "-lc",
        f"exec {sandbox._quote(chrome)} {flags} about:blank >/tmp/pantheon-browser.log 2>&1",
        timeout=30)
    if code != 0:
        raise SandboxBrowserError(f"couldn't start the browser in the sandbox: {out[-300:]}")
    for _ in range(30):
        if await _version():
            return
        await asyncio.sleep(1)
    _, log_tail = await _sh("tail -5 /tmp/pantheon-browser.log")
    raise SandboxBrowserError("the sandbox browser didn't start: " + log_tail[-300:])


class Tunnel:
    """A local port whose connections reach the container's 127.0.0.1:PORT."""

    def __init__(self) -> None:
        self.server: asyncio.AbstractServer | None = None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # no console window
        proc = await asyncio.create_subprocess_exec(
            sandbox.docker_cli() or "docker", "exec", "-i", sandbox.CONTAINER, "socat", "-",
            f"TCP:127.0.0.1:{PORT}", stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **kwargs)
        assert proc.stdin is not None and proc.stdout is not None

        async def up() -> None:
            try:
                while data := await reader.read(65536):
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            finally:
                proc.stdin.close()

        async def down() -> None:
            try:
                while data := await proc.stdout.read(65536):
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()

        try:
            await asyncio.gather(up(), down(), return_exceptions=True)
        finally:
            if proc.returncode is None:
                proc.kill()

    def close(self) -> None:
        if self.server is not None:
            self.server.close()


async def connect(pw: Any) -> Any:
    """A Playwright Browser for the sandbox's Chromium, starting the sandbox and the
    browser when needed."""
    global _tunnel
    async with _lock:
        try:
            await sandbox.ensure_running()
        except sandbox.SandboxError as e:
            raise SandboxBrowserError(str(e)) from None
        info = await _version()
        if info is None:
            await _start_chrome()
            info = await _version()
        if not info or "webSocketDebuggerUrl" not in info:
            raise SandboxBrowserError("couldn't reach the sandbox browser")
        if _tunnel is None:
            _tunnel = Tunnel()
            await _tunnel.start()
        path = "/" + info["webSocketDebuggerUrl"].split("/", 3)[3]
        try:
            return await pw.chromium.connect_over_cdp(f"ws://127.0.0.1:{_tunnel.port}{path}",
                                                      timeout=30_000)
        except Exception as e:  # noqa: BLE001 - shown to the agent
            raise SandboxBrowserError(f"couldn't connect to the sandbox browser: {e}") from None


def stop() -> None:
    global _tunnel
    if _tunnel is not None:
        _tunnel.close()
        _tunnel = None
