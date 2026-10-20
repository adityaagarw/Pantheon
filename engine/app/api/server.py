"""Server mode: Pantheon in a browser on a chosen port (services/server.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.services import server

router = APIRouter(prefix="/api/v1/server", tags=["server"])


@router.get("")
async def status() -> dict[str, Any]:
    return server.status()


@router.put("")
async def configure(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await server.configure(
            enabled=body.get("enabled"), port=body.get("port"),
            new_access_code=bool(body.get("newAccessCode")))
    except (TypeError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
