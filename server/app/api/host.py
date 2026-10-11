"""Endpoints for Pantheon Host (the helper on the user's computer), and its status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, Response

from app.services import host
from app.tools.base import ToolError

router = APIRouter(prefix="/api/v1/host", tags=["host"])


def _auth(token: str | None) -> None:
    if not host.check_token(token):
        raise HTTPException(401, "bad Pantheon Host token (it reads it from data/host.token)")


@router.post("/hello")
async def hello(body: dict[str, Any] = Body(...),
                x_host_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_host_token)
    host.bridge.hello(body)
    return {"ok": True}


@router.get("/next")
async def next_job(wait: float = Query(default=25, ge=0, le=55),
                   x_host_token: str | None = Header(default=None)) -> Any:
    _auth(x_host_token)
    job = await host.bridge.next_job(wait)
    return job if job else Response(status_code=204)


@router.post("/result")
async def result(body: dict[str, Any] = Body(...),
                 x_host_token: str | None = Header(default=None)) -> dict[str, Any]:
    _auth(x_host_token)
    host.bridge.resolve(str(body.get("id", "")), bool(body.get("ok")), body.get("result"),
                          body.get("error"))
    return {"ok": True}


@router.get("/status")
async def status() -> dict[str, Any]:
    return host.bridge.status()


async def _ask(op: str, args: dict[str, Any], timeout: float) -> dict[str, Any]:
    if not host.bridge.can("folders"):
        raise HTTPException(409, "Pantheon Host isn't running on your computer (python host/main.py)")
    try:
        return {"message": await host.bridge.call(op, args, timeout=timeout)}
    except ToolError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/folders/pick")
async def pick_folder() -> dict[str, Any]:
    """Pantheon Host shows a folder picker on the user's screen; only they choose."""
    return await _ask("pick_folder", {}, timeout=600)


@router.post("/folders/remove")
async def remove_folder(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return await _ask("disallow", {"path": str(body.get("path") or "")}, timeout=30)


async def _running_work() -> list[str]:
    """What a backend restart would stop: agents at work and benchmark runs."""
    from sqlalchemy import select

    from app.core.db import SessionLocal
    from app.models import Agent, BenchRun, Org
    from app.services.bench import BUSY

    async with SessionLocal() as session:
        busy = (await session.execute(select(Agent.name, Org.name).join(Org, Org.id == Agent.org_id).where(
            Agent.runtime_status.in_(BUSY), Org.kind != "bench"))).all()
        runs = (await session.execute(select(BenchRun.org_name).where(
            BenchRun.status == "running"))).scalars().all()
    return ([f"{a} ({o}) is working" for a, o in busy][:8]
            + [f"a benchmark of {o} is running" for o in runs])


@router.post("/workspace")
async def pick_workspace(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """An organization's workspace from a folder on the user's computer. Pantheon Host shows a
    folder picker on their screen and, for a folder Docker doesn't share yet, mounts it and
    restarts the backend (which stops running work, so that needs ``force``)."""
    from app.core.db import SessionLocal
    from app.models import Org
    from app.services import orgs

    async with SessionLocal() as session:
        org = await session.get(Org, str(body.get("orgId") or ""))
    if org is None:
        raise HTTPException(404, "org not found")
    if not host.bridge.can("workspaces"):
        raise HTTPException(409, "Pantheon Host isn't running on your computer, or it's an older "
                                 "version: start or restart it (python host/main.py)")
    if not body.get("force") and (running := await _running_work()):
        raise HTTPException(409, {"restart": True, "running": running})
    try:
        got = await host.bridge.call("pick_workspace", {"org": org.name}, timeout=600)
    except ToolError as e:
        raise HTTPException(400, str(e)) from None
    updated = await orgs.update_org(org.id, {
        "workspace": got["path"],
        "settings": {"workspace_host": {"path": got["path"], "hostPath": got["hostPath"]}}})
    return {"workspace": updated.workspace, "hostPath": got["hostPath"],
            "restart": bool(got.get("restart"))}
