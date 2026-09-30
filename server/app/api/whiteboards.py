"""Whiteboards: list, create, load, merge-save, agent shape queue, thumbnails."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from app.core.db import SessionLocal
from app.models import Org
from app.services import whiteboards as wb

router = APIRouter(prefix="/api/v1", tags=["whiteboards"])


async def _board(board_id: str):
    b = await wb.get(board_id)
    if b is None:
        raise HTTPException(404, "whiteboard not found")
    return b


@router.get("/orgs/{org_id}/whiteboards")
async def list_boards(org_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, "organization not found")
    return [wb.to_dict(b) for b in await wb.for_org(org_id)]


@router.post("/orgs/{org_id}/whiteboards", status_code=201)
async def create_board(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return wb.to_dict(await wb.create(org_id, str(body.get("title") or "Whiteboard")))


@router.get("/whiteboards/{board_id}")
async def get_board(board_id: str) -> dict[str, Any]:
    return wb.to_dict(await _board(board_id), scene=True)


@router.put("/whiteboards/{board_id}/scene")
async def save_board(board_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    elements = body.get("elements")
    if not isinstance(elements, list):
        raise HTTPException(400, "elements must be a list")
    try:
        b = await wb.save_scene(board_id, elements, app_state=body.get("appState"),
                                files=body.get("files"), by="user")
    except wb.WhiteboardError as e:
        raise HTTPException(400, str(e)) from None
    return wb.to_dict(b, scene=True)


@router.post("/whiteboards/{board_id}/pending/claim")
async def claim_pending(board_id: str) -> dict[str, Any]:
    try:
        return {"pending": await wb.claim_pending(board_id)}
    except wb.WhiteboardError as e:
        raise HTTPException(404, str(e)) from None


@router.put("/whiteboards/{board_id}/thumbnail", status_code=204)
async def put_thumbnail(board_id: str, body: dict[str, Any] = Body(...)) -> None:
    try:
        await wb.set_thumbnail(board_id, str(body.get("dataUrl") or ""))
    except wb.WhiteboardError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/whiteboards/{board_id}/thumbnail.png")
async def get_thumbnail(board_id: str) -> Response:
    png = wb.thumbnail_png(await _board(board_id))
    if png is None:
        raise HTTPException(404, "no thumbnail yet")
    return Response(png, media_type="image/png", headers={"Cache-Control": "no-cache"})
