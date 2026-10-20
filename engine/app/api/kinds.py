"""Agent kinds (app/agents/kinds.py), and answering as a person (services/humans.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.agents import kinds
from app.services import humans
from app.services.comms import message_to_dict

router = APIRouter(prefix="/api/v1", tags=["agent kinds"])


@router.get("/agent-kinds")
async def list_kinds() -> list[dict[str, Any]]:
    return [k.info() for k in kinds.all_kinds()]


@router.post("/agent-kinds/{kind_id}/check")
async def check_kind(kind_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The Kind tab's Check: is the program installed, signed in and answering, with which
    models? ``{"runner": {...}}``, the settings as they stand in the editor."""
    k = kinds.get(kind_id)
    if k is None:
        raise HTTPException(404, f"no agent kind '{kind_id}'")
    if k.check is None:
        return {"ok": True, "checks": [], "note": "nothing to check for this kind"}
    try:
        return await k.check(dict(body.get("runner") or {}))
    except Exception as e:  # noqa: BLE001 - shown in the editor
        return {"ok": False, "checks": [{"label": "Check", "ok": False, "detail": str(e)[:600]}]}


@router.get("/agents/{agent_id}/human")
async def human_waiting(agent_id: str) -> dict[str, Any]:
    try:
        msgs = await humans.waiting(agent_id)
    except humans.HumanError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from None
    return {"waiting": [message_to_dict(m) for m in msgs]}


@router.post("/agents/{agent_id}/human/answer")
async def human_answer(agent_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await humans.answer(agent_id, str(body.get("content") or ""),
                                   body.get("messageIds") or None)
    except humans.HumanError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from None
