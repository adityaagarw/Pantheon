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
