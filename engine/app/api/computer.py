"""Which computer agents use (app/computer/__init__.py), who's using it, and Stop."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, WebSocket
from fastapi.responses import Response

from app import computer

router = APIRouter(prefix="/api/v1/computer", tags=["computer"])


@router.get("")
async def status() -> dict[str, Any]:
    from app.tools.computer import _lease

    return {**computer.status(), "inUseBy": _lease.get("name") if _lease else None}


@router.put("")
async def choose(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        await computer.set_backend(str(body.get("backend") or "off"))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return await status()


@router.post("/stop")
async def stop() -> dict[str, Any]:
    """Take the computer back: computer use pauses and the agent using it stops."""
    from app.agents.runtime import runtime
    from app.tools.computer import _lease

    who = computer.active.get("agentId") or _lease.get("agent")
    computer.pause("you pressed Stop")
    _lease.clear()
    if who:
        await runtime.stop_turn(str(who))
    return await status()


_preview: dict[str, Any] = {"at": 0.0, "data": None}


@router.get("/preview")
async def preview() -> dict[str, Any]:
    """A fresh picture of what the agent is working on (this computer, through cua-driver):
    the window it acted on last, or the whole screen. At most one capture a second."""
    import time

    from app.computer.driver import DriverError, driver

    if computer.backend_id() not in ("driver", "cua"):
        return {"image": None, "reason": "no computer to show"}
    if computer.backend_id() == "cua":
        from app.computer import sandbox

        try:
            shot = await sandbox.computer().screenshot()
        except Exception as e:  # noqa: BLE001 - shown in the view
            return {"image": None, "reason": str(e)}
        active = dict(computer.active)
        return {"image": shot, "where": "the sandbox desktop", "agent": active.get("agent"),
                "working": bool(active) and time.time() - active.get("at", 0) < 30}
    now = time.monotonic()
    if _preview["data"] is not None and now - _preview["at"] < 1.0:
        return _preview["data"]
    active = dict(computer.active)
    picture, where = None, "the screen"
    try:
        if active.get("windowId"):
            _, images, error = await driver.call("get_window_state", {
                "pid": active["pid"], "window_id": active["windowId"], "display_only": True,
                "include_accessibility_tree": False, "max_dimension": 1280,
                "session": "pantheon-preview"})
            if images and not error:
                picture, where = images[0], "its window"
        if picture is None:
            _, images, _ = await driver.call("get_desktop_state", {
                "max_image_dimension": 1280, "session": "pantheon-preview"})
            picture = images[0] if images else None
    except DriverError as e:
        return {"image": None, "reason": str(e)}
    data = {"image": picture, "where": where, "agent": active.get("agent"),
            "working": bool(active) and time.time() - active.get("at", 0) < 30}
    _preview.update(at=now, data=data)
    return data


# --- where agents browse (app/browser/sandboxed.py) ---------------------------------------


@router.get("/browser")
async def browser_where() -> dict[str, Any]:
    from app.browser import sandboxed

    return {"where": await sandboxed.where(), "sandbox": await sandboxed.available()}


@router.put("/browser")
async def browser_choose(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from app.browser import sandboxed

    try:
        await sandboxed.set_where(str(body.get("where") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return await browser_where()


# --- the sandbox desktop (app/computer/sandbox.py) ---------------------------------------


@router.get("/sandbox")
async def sandbox_status() -> dict[str, Any]:
    from app.computer import sandbox

    return await sandbox.status()


@router.post("/sandbox/setup")
async def sandbox_setup() -> dict[str, Any]:
    """Download the desktop and create the container, in the background (poll the status)."""
    import asyncio

    from app.computer import sandbox

    asyncio.create_task(sandbox.setup(), name="sandbox-setup")
    await asyncio.sleep(0.2)
    return await sandbox.status()


@router.post("/sandbox/start")
async def sandbox_start() -> dict[str, Any]:
    from app.computer import sandbox

    try:
        await sandbox.ensure_running()
    except sandbox.SandboxError as e:
        raise HTTPException(400, str(e)) from None
    return await sandbox.status()


async def _desktop() -> tuple[str, dict[str, str]]:
    """Where the sandbox's KasmVNC listens, and the header that signs in to it."""
    import base64

    from app.computer import sandbox

    st = await sandbox._load()
    if not st.get("vnc_port"):
        raise HTTPException(404, "the sandbox desktop isn't set up")
    auth = base64.b64encode(f"kasm_user:{st.get('vnc_password', '')}".encode()).decode()
    return f"127.0.0.1:{st['vnc_port']}", {"Authorization": f"Basic {auth}"}


@router.websocket("/sandbox/desktop/websockify")
async def desktop_socket(ws: WebSocket) -> None:
    """The desktop's live connection, relayed: the browser never needs the VNC password, and
    the desktop is reachable wherever Pantheon is (server mode included)."""
    import asyncio

    import websockets

    try:
        host, headers = await _desktop()
    except HTTPException:
        await ws.close(code=4404)
        return
    asked = [p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()]
    try:
        # KasmVNC answers 404 to a connection without an Origin.
        upstream = await websockets.connect(f"ws://{host}/websockify", additional_headers=headers,
                                            origin=f"http://{host}",  # type: ignore[arg-type]
                                            subprotocols=asked or None, max_size=None,
                                            ping_interval=None, open_timeout=10)
    except Exception:  # noqa: BLE001 - the desktop isn't running or refused us
        await ws.close(code=4502)
        return
    await ws.accept(subprotocol=upstream.subprotocol)

    async def up() -> None:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            await upstream.send(msg["bytes"] if msg.get("bytes") is not None else msg.get("text") or "")

    async def down() -> None:
        async for data in upstream:
            if isinstance(data, bytes):
                await ws.send_bytes(data)
            else:
                await ws.send_text(data)

    tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
        try:
            await ws.close()
        except RuntimeError:
            pass  # already closed


@router.get("/sandbox/desktop/{path:path}")
async def desktop_page(path: str) -> Response:
    """KasmVNC's web client (its page, scripts and images), signed in by Pantheon."""
    import httpx

    host, headers = await _desktop()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(f"http://{host}/{path}", headers=headers)
    except httpx.HTTPError:
        raise HTTPException(502, "the sandbox desktop isn't running") from None
    wanted = ("content-type", "cache-control", "etag", "last-modified")
    keep = {k: v for k, v in r.headers.items() if k.lower() in wanted}
    return Response(r.content, status_code=r.status_code, headers=keep)


@router.post("/sandbox/stop")
async def sandbox_stop() -> dict[str, Any]:
    from app.computer import sandbox

    await sandbox.stop()
    return await sandbox.status()


@router.post("/docker/start")
async def docker_start() -> dict[str, Any]:
    from app.computer import sandbox

    try:
        return {"message": sandbox.start_docker()}
    except sandbox.SandboxError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/permissions")
async def permissions() -> dict[str, Any]:
    """macOS: ask for Accessibility and Screen Recording for cua-driver (the system shows its
    dialogs). Elsewhere there's nothing to grant."""
    import asyncio
    import sys

    if sys.platform != "darwin":
        return {"ok": True, "message": "Nothing to grant on this system."}
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "cua_driver", "permissions", "grant",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**__import__("os").environ, "DO_NOT_TRACK": "1"})
    out, _ = await proc.communicate()
    return {"ok": proc.returncode == 0, "message": out.decode(errors="replace")[-2000:]}


@router.post("/resume")
async def resume() -> dict[str, Any]:
    computer.resume()
    return await status()
