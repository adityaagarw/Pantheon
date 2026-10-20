"""Client for the notebook kernel (app/sandbox/nbkernel.py), reached over HTTP.

It runs on this computer, started by the engine on first use; or, when the computer agents
use is the sandbox desktop (the cua-desktop pack), inside it: copied in through the
computer-server and started there.
"""

from __future__ import annotations

import asyncio
import base64
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings

SCRIPT = Path(__file__).resolve().parent.parent / "sandbox" / "nbkernel.py"
VERSION = 1
_install_lock = asyncio.Lock()


class KernelError(RuntimeError):
    pass


LOCAL_PORT_OFFSET = 1  # the engine's port + 1
_local: subprocess.Popen | None = None


def _in_sandbox() -> bool:
    return False  # notebooks run on this computer (the sandbox exposes no kernel port)


def base_url() -> str:
    if settings.notebook_kernel_url:
        return settings.notebook_kernel_url.rstrip("/")
    if not _in_sandbox():
        return f"http://127.0.0.1:{settings.port + LOCAL_PORT_OFFSET}"
    u = urlparse(settings.computer_url)
    if u.port == 8000 or u.port is None:  # the desktop container, inside Docker
        return f"{u.scheme}://{u.hostname}:8890"
    return f"{u.scheme}://{u.hostname}:8712"  # published on this machine


async def _health() -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{base_url()}/health")
        return r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def ensure() -> None:
    """Start the kernel in the sandbox if it isn't running (or is an older version)."""
    h = await _health()
    if h and h.get("version") == VERSION:
        return
    async with _install_lock:
        h = await _health()
        if h and h.get("version") == VERSION:
            return
        if h:  # an older kernel: replace it
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(f"{base_url()}/quit", json={})
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
        if not _in_sandbox():
            _start_local()
        else:
            await _start_in_sandbox()
        for _ in range(40):
            await asyncio.sleep(0.25)
            if await _health():
                return
        raise KernelError("the notebook kernel didn't start" + (
            " in the sandbox desktop (see /tmp/pantheon-kernel/kernel.log there)" if _in_sandbox()
            else f" (see {settings.data_path / 'kernel' / 'kernel.log'})"))


def _start_local() -> None:
    """The kernel on this computer, as a child of the engine (it stops with it)."""
    global _local
    if _local is not None and _local.poll() is None:
        return
    home = settings.data_path / "kernel"
    home.mkdir(parents=True, exist_ok=True)
    log = open(home / "kernel.log", "a")  # noqa: SIM115 - the child keeps it
    env = {**os.environ, "PANTHEON_KERNEL_HOST": "127.0.0.1",
           "PANTHEON_KERNEL_PORT": str(settings.port + LOCAL_PORT_OFFSET),
           "PANTHEON_KERNEL_HOME": str(home), "PANTHEON_KERNEL_PARENT": str(os.getpid())}
    _local = subprocess.Popen([sys.executable, str(SCRIPT)], env=env, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT)


async def _start_in_sandbox() -> None:
    from app.computer.cua import ComputerError
    from app.tools.computer import computer

    script = base64.b64encode(SCRIPT.read_bytes()).decode()
    try:
        await computer().cmd("run_command", timeout=30, command=(
            "mkdir -p /tmp/pantheon-kernel && "
            f"echo {script} | base64 -d > /tmp/pantheon-kernel/nbkernel.py && "
            "PY=$(command -v /opt/venv/bin/python3 || command -v python3) && "
            "$PY /tmp/pantheon-kernel/nbkernel.py --spawn"))
    except ComputerError as e:
        raise KernelError(f"notebooks run on the sandbox desktop, which isn't reachable: {e}"
                          ) from None


async def _post(path: str, body: dict[str, Any], timeout: float = 30) -> dict[str, Any]:
    await ensure()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{base_url()}{path}", json=body)
    except httpx.HTTPError as e:
        raise KernelError(f"the notebook kernel didn't answer: {e}") from None
    if r.status_code >= 400:
        raise KernelError(f"the notebook kernel failed: {r.text[:300]}")
    return r.json()


async def execute(notebook: str, code: str, timeout: float = 300) -> dict[str, Any]:
    return await _post("/execute", {"notebook": notebook, "code": code, "timeout": timeout},
                       timeout=timeout + 30)


async def interrupt(notebook: str) -> None:
    await _post("/interrupt", {"notebook": notebook})


async def restart(notebook: str) -> None:
    await _post("/restart", {"notebook": notebook})
