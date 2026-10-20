"""Custom tools and scheduled activities (what Zeus builds; editable by the user)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException

from app.core.db import SessionLocal
from app.models import Org
from app.services import activities, customtools

router = APIRouter(prefix="/api/v1", tags=["extend"])


@router.get("/custom-tools")
async def list_custom_tools() -> list[dict[str, Any]]:
    return [customtools.to_dict(t) for t in await customtools.all_tools()]


@router.put("/custom-tools/{name}")
async def save_custom_tool(name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        row = await customtools.save(name, str(body.get("description") or ""),
                                     str(body.get("kind") or ""), body.get("parameters"),
                                     body.get("config"), str(body.get("approval") or ""))
    except customtools.CustomToolError as e:
        raise HTTPException(400, str(e)) from None
    return customtools.to_dict(row)


@router.delete("/custom-tools/{name}", status_code=204)
async def delete_custom_tool(name: str) -> None:
    try:
        await customtools.delete(name)
    except customtools.CustomToolError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/orgs/{org_id}/activities")
async def list_activities(org_id: str) -> list[dict[str, Any]]:
    return [activities.to_dict(a) for a in await activities.for_org(org_id)]


@router.post("/orgs/{org_id}/activities", status_code=201)
async def create_activity(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, "organization not found")
    try:
        a = await activities.create(org_id, str(body.get("title") or ""),
                                    str(body.get("instructions") or ""),
                                    participants=body.get("participants") or [],
                                    room=body.get("room") or None, schedule=body.get("schedule"))
    except activities.ActivityError as e:
        raise HTTPException(400, str(e)) from None
    return activities.to_dict(a)


@router.patch("/activities/{activity_id}")
async def update_activity(activity_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return activities.to_dict(await activities.update(activity_id, body))
    except activities.ActivityError as e:
        raise HTTPException(400, str(e)) from None


@router.delete("/activities/{activity_id}", status_code=204)
async def delete_activity(activity_id: str) -> None:
    try:
        await activities.delete(activity_id)
    except activities.ActivityError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/activities/{activity_id}/run")
async def run_activity(activity_id: str) -> dict[str, Any]:
    try:
        return {"notified": await activities.run(activity_id)}
    except activities.ActivityError as e:
        raise HTTPException(404, str(e)) from None
