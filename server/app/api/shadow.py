"""Shadowing: record the user's work for an agent to learn, and the playbooks agents keep."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException
from fastapi.responses import FileResponse

from app.core.db import SessionLocal
from app.models import Agent
from app.services import shadow
from app.services.shadow import ShadowError

router = APIRouter(prefix="/api/v1", tags=["shadow"])


def _bad(e: ShadowError) -> HTTPException:
    return HTTPException(400, str(e))


@router.post("/orgs/{org_id}/shadow", status_code=201)
async def start(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        s = await shadow.create_session(org_id, str(body.get("agentId", "")),
                                        str(body.get("title", "")), str(body.get("source", "")),
                                        str(body.get("note", "")))
    except ShadowError as e:
        raise _bad(e) from None
    return shadow.session_to_dict(s)


@router.get("/orgs/{org_id}/shadow")
async def list_recordings(org_id: str) -> list[dict[str, Any]]:
    return await shadow.list_sessions(org_id)


# Before /shadow/{session_id}, which would otherwise match "pending".
@router.get("/shadow/pending")
async def pending(source: str = "browser") -> dict[str, Any]:
    """A recording waiting for a recorder (the extension offers it without a code)."""
    s = await shadow.pending(source)
    if s is None:
        return {"session": None}
    async with SessionLocal() as session:
        agent = await session.get(Agent, s.agent_id)
    return {"session": {"sessionId": s.id, "title": s.title, "code": s.code,
                        "agentName": agent.name if agent else None}}


@router.get("/shadow/{session_id}")
async def get_recording(session_id: str) -> dict[str, Any]:
    try:
        s = await shadow.get_session(session_id)
    except ShadowError as e:
        raise HTTPException(404, str(e)) from None
    async with SessionLocal() as session:
        agent = await session.get(Agent, s.agent_id)
    return {**shadow.session_to_dict(s, agent.name if agent else None),
            "events": [shadow.event_to_dict(e) for e in await shadow.events(s.id, limit=2000)]}


@router.post("/shadow/{session_id}/stop")
async def stop(session_id: str) -> dict[str, Any]:
    try:
        return shadow.session_to_dict(await shadow.stop(session_id))
    except ShadowError as e:
        raise _bad(e) from None


@router.post("/shadow/{session_id}/learn")
async def learn(session_id: str) -> dict[str, Any]:
    try:
        return shadow.session_to_dict(await shadow.learn(session_id))
    except ShadowError as e:
        raise _bad(e) from None


@router.delete("/shadow/{session_id}", status_code=204)
async def delete(session_id: str) -> None:
    try:
        await shadow.delete_session(session_id)
    except ShadowError as e:
        raise HTTPException(404, str(e)) from None


@router.get("/shadow/{session_id}/images/{name}")
async def image(session_id: str, name: str) -> FileResponse:
    folder = shadow.images_dir(session_id).resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


# --- recorders (browser extension, desktop app) --------------------------------------------


@router.post("/shadow/connect")
async def connect(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """A recorder exchanges the session code the user typed for the session it records."""
    try:
        s = await shadow.by_code(str(body.get("code", "")))
    except ShadowError as e:
        raise HTTPException(404, str(e)) from None
    return {"sessionId": s.id, "title": s.title, "source": s.source}


@router.post("/shadow/{session_id}/events")
async def post_events(session_id: str, body: dict[str, Any] = Body(...),
                      x_shadow_code: str | None = Header(default=None)) -> dict[str, Any]:
    try:
        s = await shadow.get_session(session_id)
    except ShadowError as e:
        raise HTTPException(404, str(e)) from None
    if not x_shadow_code or x_shadow_code.strip().upper() != s.code:
        raise HTTPException(401, "wrong session code")
    if s.status != "recording":
        return {"recording": False, "kept": 0}
    items = body.get("events")
    if not isinstance(items, list):
        raise HTTPException(400, "events must be a list")
    try:
        kept = await shadow.add_events(s.id, items)
    except ShadowError:
        return {"recording": False, "kept": 0}
    return {"recording": True, "kept": kept}


# --- playbooks ------------------------------------------------------------------------------


@router.get("/orgs/{org_id}/playbooks")
async def list_playbooks(org_id: str, agentId: str | None = None) -> list[dict[str, Any]]:  # noqa: N803
    return [shadow.playbook_to_dict(p) for p in await shadow.playbooks(org_id, agentId)]


@router.patch("/playbooks/{playbook_id}")
async def update_playbook(playbook_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return shadow.playbook_to_dict(await shadow.update_playbook(playbook_id, body))
    except ShadowError as e:
        raise HTTPException(404, str(e)) from None


@router.delete("/playbooks/{playbook_id}", status_code=204)
async def delete_playbook(playbook_id: str) -> None:
    await shadow.delete_playbook(playbook_id)
