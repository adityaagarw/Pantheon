"""Meetings API: list, inspect, and let the user convene a meeting."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Agent, Meeting, Org
from app.services import meetings

router = APIRouter(prefix="/api/v1", tags=["meetings"])
log = logging.getLogger(__name__)
_running: set[asyncio.Task] = set()


@router.get("/orgs/{org_id}/meetings")
async def list_meetings(org_id: str, limit: int = 100) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Meeting).where(Meeting.org_id == org_id)
                                      .order_by(Meeting.created_at.desc())
                                      .limit(min(limit, 500)))).scalars().all()
    return [meetings.meeting_to_dict(m) for m in rows]


@router.get("/meetings/{meeting_id}")
async def get_meeting(meeting_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        m = await session.get(Meeting, meeting_id)
    if m is None:
        raise HTTPException(404, "meeting not found")
    return meetings.meeting_to_dict(m)


@router.post("/meetings/{meeting_id}/end")
async def end_meeting(meeting_id: str) -> dict[str, Any]:
    """End a meeting that is stuck in progress."""
    row = await meetings.end_meeting(meeting_id)
    if row is None:
        raise HTTPException(404, "no meeting in progress with that id")
    return {"id": row.id, "status": row.status}


@router.post("/orgs/{org_id}/meetings", status_code=202)
async def call_meeting(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The user convenes a meeting; an agent facilitates and writes the minutes."""
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        facilitator = await session.get(Agent, body.get("facilitatorId") or "")
    if org is None:
        raise HTTPException(404, "org not found")
    if facilitator is None or facilitator.org_id != org_id:
        raise HTTPException(400, "choose an agent from this org to facilitate")
    try:
        req = await meetings.prepare(
            org, facilitator, list(body.get("participants") or []), body.get("agenda", ""),
            style=body.get("style"), rounds=body.get("rounds"),
            detail=body.get("detail", "brief"), create_tasks=body.get("createTasks"),
            room=body.get("room"), called_by="user",
        )
    except meetings.MeetingError as e:
        raise HTTPException(400, str(e)) from None

    async def go() -> None:
        try:
            await meetings.run(req)
        except Exception:  # noqa: BLE001 - failures are recorded on the meeting row
            log.exception("user-called meeting failed")

    task = asyncio.create_task(go(), name=f"meeting:{org_id}")
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"status": "started", "participants": [a.id for a in req.participants],
            "rounds": req.rounds, "style": req.style}
