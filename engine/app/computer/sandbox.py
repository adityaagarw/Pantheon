"""The sandbox desktop: a Linux desktop in Docker that agents can use freely instead of the
user's own computer (the cua-desktop pack offers it as a computer).

It's trycua's cua-ubuntu image, which runs cua-spacesd: cua-driver's tools over MCP
(/mcp), behind a token, on port 3211, and a browser remote desktop (KasmVNC) on 6901. Both
are published on 127.0.0.1 only. Pantheon sets it up when the user picks it: it checks for
Docker (and can start Docker Desktop), downloads the image, and creates one container,
``pantheon-sandbox``, with a token and a desktop password of its own. It starts the
container whenever an agent needs it.

Agents get both kinds of tools here: the simple screenshot-and-click loop (computer_*,
through the driver's whole-desktop actions; SandboxComputer below) and the precise
desktop_* tools. Shell commands run with docker exec.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.computer.driver import Driver, DriverError

log = logging.getLogger(__name__)
IMAGE = "trycua/cua-ubuntu:latest"
CONTAINER = "pantheon-sandbox"
SETTING = "sandbox"
SESSION = "pantheon-sandbox"
DESKTOP = {"kind": "desktop", "display_id": "primary"}
_state: dict[str, Any] = {}
_setup: dict[str, Any] = {"running": False, "step": "", "progress": 0.0, "error": None}
_driver: Driver | None = None


class SandboxError(RuntimeError):
    pass


# --- docker -------------------------------------------------------------------------------


def docker_cli() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for p in (r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
              "/usr/local/bin/docker", "/opt/homebrew/bin/docker",
              "/Applications/Docker.app/Contents/Resources/bin/docker"):
        if Path(p).exists():
            return p
    return None


async def _docker(*args: str, timeout: float = 60) -> tuple[int, str]:
    cli = docker_cli()
    if cli is None:
        raise SandboxError("Docker isn't installed")
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # no console window
    proc = await asyncio.create_subprocess_exec(
        cli, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, **kwargs)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, f"docker {args[0]} took longer than {int(timeout)} s"
    return proc.returncode or 0, out.decode("utf-8", errors="replace").strip()


async def docker_running() -> bool:
    if docker_cli() is None:
        return False
    code, _ = await _docker("info", "--format", "{{.ServerVersion}}", timeout=20)
    return code == 0


def start_docker() -> str:
    """Start Docker Desktop (Windows, macOS). Returns what was done."""
    if sys.platform == "win32":
        exe = Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe")
        if exe.exists():
            subprocess.Popen([str(exe)], close_fds=True)
            return "Starting Docker Desktop…"
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-a", "Docker"])
        return "Starting Docker Desktop…"
    raise SandboxError("start Docker yourself (on Linux: sudo systemctl start docker)")


# --- state ----------------------------------------------------------------------------------


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


async def _load() -> dict[str, Any]:
    from app.core.db import SessionLocal
    from app.models import Setting

    if not _state:
        async with SessionLocal() as session:
            row = await session.get(Setting, SETTING)
        _state.update(row.value if row and isinstance(row.value, dict) else {})
    return _state


async def _save() -> None:
    from app.core.db import SessionLocal
    from app.models import Setting

    async with SessionLocal() as session:
        row = await session.get(Setting, SETTING)
        if row is None:
            session.add(Setting(key=SETTING, value=dict(_state)))
        else:
            row.value = dict(_state)
        await session.commit()


async def container_state() -> str:
    """none, running, or stopped (any other state)."""
    code, out = await _docker("inspect", "-f", "{{.State.Status}}", CONTAINER, timeout=20)
    if code != 0:
        return "none"
    return "running" if out == "running" else "stopped"


async def healthy() -> bool:
    import httpx

    st = await _load()
    if not st.get("mcp_port"):
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"http://127.0.0.1:{st['mcp_port']}/health")
        return r.status_code in (200, 204)
    except httpx.HTTPError:
        return False


async def status() -> dict[str, Any]:
    st = await _load()
    out: dict[str, Any] = {"docker": "missing", "image": False, "container": "none",
                           "healthy": False, "setup": dict(_setup), "image_name": IMAGE}
    if docker_cli() is None:
        return out
    if not await docker_running():
        out["docker"] = "stopped"
        return out
    out["docker"] = "running"
    code, _ = await _docker("image", "inspect", IMAGE, timeout=20)
    out["image"] = code == 0
    out["container"] = await container_state()
    if out["container"] != "none" and not st.get("token"):
        out["container"] = "none"  # made by another Pantheon (or before a reset): set up again
    out["healthy"] = out["container"] == "running" and await healthy()
    if st.get("vnc_port"):
        # The desktop in a browser, through Pantheon (api/computer.py signs in to it).
        out["view"] = {"url": "/api/v1/computer/sandbox/desktop/index.html"}
    return out


# --- setup ----------------------------------------------------------------------------------


async def setup() -> None:
    """Download the image (if needed) and create the container. Runs in the background;
    status()["setup"] reports progress."""
    if _setup["running"]:
        return
    _setup.update(running=True, step="Checking Docker", progress=0.0, error=None)
    try:
        if not await docker_running():
            raise SandboxError("Docker isn't running")
        code, _ = await _docker("image", "inspect", IMAGE, timeout=20)
        if code != 0:
            await _pull()
        _setup.update(step="Creating the sandbox", progress=0.95)
        await _create()
        _setup.update(step="Starting the desktop", progress=0.98)
        await ensure_running()
        # The image's Firefox asks for a profile on first start; have one ready.
        await _docker("exec", "-u", "1000", CONTAINER, "bash", "-lc",
                      "ls ~/.mozilla/firefox/*.default* >/dev/null 2>&1 || "
                      "firefox -CreateProfile default >/dev/null 2>&1 || true", timeout=60)
        _setup.update(step="Ready", progress=1.0)
    except (SandboxError, DriverError) as e:
        _setup["error"] = str(e)
    except Exception as e:  # noqa: BLE001 - shown to the user
        log.exception("sandbox setup failed")
        _setup["error"] = f"{type(e).__name__}: {e}"
    finally:
        _setup["running"] = False


async def _pull() -> None:
    """docker pull, with progress from its per-layer lines (complete layers / layers)."""
    cli = docker_cli()
    _setup.update(step="Downloading the sandbox (about 1.8 GB)", progress=0.01)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    proc = await asyncio.create_subprocess_exec(
        cli, "pull", IMAGE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        **kwargs)
    layers: dict[str, str] = {}
    tail: list[str] = []
    assert proc.stdout is not None
    async for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").strip()
        tail = (tail + [line])[-5:]
        m = re.match(r"^([0-9a-f]{12}): (.+)$", line)
        if m:
            layers[m.group(1)] = m.group(2)
            done = sum(1 for v in layers.values() if v.startswith(("Pull complete", "Already exists")))
            _setup["progress"] = round(0.02 + 0.9 * done / max(1, len(layers)), 3)
    if await proc.wait() != 0:
        raise SandboxError("downloading the sandbox failed: " + " ".join(tail)[-300:])


async def _create() -> None:
    st = await _load()
    if await container_state() != "none":
        if st.get("token"):
            return
        await _docker("rm", "-f", CONTAINER, timeout=60)  # not ours to talk to: replace it
    st.setdefault("token", secrets.token_hex(24))
    st.setdefault("vnc_password", secrets.token_urlsafe(9))
    st["mcp_port"] = _free_port(int(st.get("mcp_port") or 18711))
    st["vnc_port"] = _free_port(int(st.get("vnc_port") or 16901))
    await _save()
    code, out = await _docker(
        "create", "--name", CONTAINER, "--shm-size=1g",
        "-p", f"127.0.0.1:{st['mcp_port']}:3211", "-p", f"127.0.0.1:{st['vnc_port']}:6901",
        "-e", f"CUA_ENV_TOKEN={st['token']}", "-e", f"VNC_PW={st['vnc_password']}",
        "--label", "app=pantheon", IMAGE, timeout=120)
    if code != 0:
        raise SandboxError(f"couldn't create the sandbox: {out[-300:]}")


async def ensure_running() -> None:
    """Start the container when it's stopped and wait until the desktop answers."""
    state = await container_state()
    if state == "none":
        raise SandboxError("the sandbox isn't set up: Settings → This computer → Sandbox desktop")
    if state != "running":
        code, out = await _docker("start", CONTAINER, timeout=60)
        if code != 0:
            raise SandboxError(f"couldn't start the sandbox: {out[-300:]}")
    for _ in range(60):
        if await healthy():
            return
        await asyncio.sleep(1)
    raise SandboxError("the sandbox desktop didn't come up within a minute")


async def stop() -> None:
    global _driver
    if _driver is not None:
        await _driver.stop()
        _driver = None
    if await container_state() == "running":
        await _docker("stop", "-t", "5", CONTAINER, timeout=60)


async def remove() -> None:
    await stop()
    await _docker("rm", "-f", CONTAINER, timeout=60)


async def driver() -> Driver:
    """The sandbox's cua-driver, starting the container when needed."""
    global _driver
    from app.tools.base import ToolError

    try:
        if not await docker_running():
            raise SandboxError("Docker isn't running: start Docker Desktop")
        await ensure_running()
    except SandboxError as e:
        raise ToolError(str(e)) from None
    st = await _load()
    if not st.get("token"):
        raise ToolError("the sandbox isn't set up: Settings → This computer → Sandbox desktop")
    url = f"http://127.0.0.1:{st['mcp_port']}/mcp"
    if _driver is None or _driver.url != url:
        _driver = Driver(url=url, token=st["token"])
    return _driver


# --- the computer_* tools -----------------------------------------------------------------


class SandboxComputer:
    """The commands computer_* tools send (as Pantheon 1's computer-server took them), done
    with the driver's whole-desktop actions in the sandbox."""

    MAX_SIDE = 1366

    def __init__(self) -> None:
        self.cursor = (0, 0)

    async def _call(self, tool: str, args: dict[str, Any]) -> str:
        from app.computer.cua import ComputerError

        d = await driver()
        try:
            text, _, error = await d.call(tool, {**args, "session": SESSION})
        except DriverError as e:
            raise ComputerError(str(e)) from None
        if error:
            raise ComputerError(text)
        return text

    async def cmd(self, action: str, /, timeout: float = 60, **p: Any) -> dict[str, Any]:
        """``action`` is positional-only: run_command passes the shell text as ``command``."""
        x, y = int(p.get("x", self.cursor[0])), int(p.get("y", self.cursor[1]))
        if action in ("left_click", "right_click", "double_click"):
            self.cursor = (x, y)
            args = {"target": DESKTOP, "x": x, "y": y,
                    "button": "right" if action == "right_click" else "left",
                    "count": 2 if action == "double_click" else 1}
            return {"success": True, "detail": await self._call("click", args)}
        if action == "move_cursor":
            self.cursor = (x, y)
            return {"success": True}
        if action == "drag_to":
            fx, fy = self.cursor
            self.cursor = (x, y)
            await self._call("drag", {"target": DESKTOP, "from_x": fx, "from_y": fy,
                                      "to_x": x, "to_y": y})
            return {"success": True}
        if action in ("scroll_up", "scroll_down"):
            await self._call("scroll", {"target": DESKTOP, "x": self.cursor[0],
                                        "y": self.cursor[1],
                                        "direction": "up" if action == "scroll_up" else "down",
                                        "amount": int(p.get("clicks") or 3)})
            return {"success": True}
        if action == "type_text":
            await self._call("type_text", {"scope": "desktop", "text": p["text"]})
            return {"success": True}
        if action == "press_key":
            await self._call("press_key", {"scope": "desktop", "key": p["key"]})
            return {"success": True}
        if action == "hotkey":
            await self._call("hotkey", {"scope": "desktop", "keys": p["keys"]})
            return {"success": True}
        if action == "open":
            return await self.run_command(f"xdg-open {_quote(p['target'])} >/dev/null 2>&1 &")
        if action == "run_command":
            return await self.run_command(p["command"], timeout)
        from app.computer.cua import ComputerError

        raise ComputerError(f"the sandbox can't do '{action}'")

    async def run_command(self, command: str, timeout: float = 180) -> dict[str, Any]:
        from app.computer.cua import ComputerError

        try:
            await ensure_running()
        except SandboxError as e:
            raise ComputerError(str(e)) from None
        code, out = await _docker("exec", "-u", "1000", CONTAINER, "bash", "-lc", command,
                                  timeout=timeout)
        return {"success": code == 0, "stdout": out, "stderr": "", "return_code": code}

    async def screenshot(self, quality: int = 70) -> str:
        from app.computer.cua import ComputerError

        d = await driver()
        try:
            _, images, error = await d.call("get_desktop_state", {
                "max_image_dimension": self.MAX_SIDE, "session": SESSION})
        except DriverError as e:
            raise ComputerError(str(e)) from None
        if error or not images:
            raise ComputerError("couldn't take a screenshot of the sandbox")
        return images[0]

    async def status(self) -> dict[str, Any]:
        st = await status()
        return {"ok": bool(st.get("healthy")), "error": None if st.get("healthy") else
                "the sandbox isn't running"}


def _quote(text: str) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


_computer = SandboxComputer()


def computer() -> SandboxComputer:
    return _computer


__all__ = ["computer", "driver", "setup", "status", "stop", "remove", "start_docker",
           "ensure_running", "SandboxError"]
