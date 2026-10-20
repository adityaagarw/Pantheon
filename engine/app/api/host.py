"""This computer, built in (Pantheon Host in Pantheon 1): its status and the folders Kronos
may use."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app import host
from app.services.host import bridge

router = APIRouter(prefix="/api/v1/host", tags=["host"])


@router.get("/status")
async def status() -> dict[str, Any]:
    return await bridge.status()


@router.post("/folders")
async def add_folder(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Allow a folder (the user picks it with the folder picker in the app)."""
    p = Path(str(body.get("path") or "")).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise HTTPException(400, f"no folder {p} on this computer")
    await host.set_folders([*host.allowed_roots(), p])
    return await bridge.status()


@router.post("/folders/remove")
async def remove_folder(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    p = Path(str(body.get("path") or "")).expanduser().resolve()
    await host.set_folders([f for f in host.allowed_roots() if f != p])
    return await bridge.status()
