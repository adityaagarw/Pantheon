"""Uploading, listing, downloading and deleting files given to agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.db import SessionLocal
from app.models import Agent, Org
from app.services import attachments as att

router = APIRouter(prefix="/api/v1", tags=["files"])


@router.post("/orgs/{org_id}/attachments", status_code=201)
async def upload(org_id: str, file: UploadFile = File(...),
                 agent_id: str = Form("")) -> dict[str, Any]:
    """Add a file to an agent's library (``agent_id``), or share it with the whole org."""
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, "organization not found")
        if agent_id:
            agent = await session.get(Agent, agent_id)
            if agent is None or agent.org_id != org_id:
                raise HTTPException(404, "agent not found in this organization")
    data = await file.read(att.settings.max_upload_bytes + 1)
    try:
        a = await att.save_upload(org_id, agent_id or None, file.filename or "file", data,
                                  file.content_type or "")
    except att.AttachmentError as e:
        raise HTTPException(413 if "at most" in str(e) else 400, str(e)) from None
    return att.to_dict(a)


@router.get("/orgs/{org_id}/attachments")
async def list_files(org_id: str, agent_id: str = "", scope: str = "all") -> list[dict[str, Any]]:
    """scope: all (everything in the org) | agent (with agent_id: its own library) | shared."""
    return [att.to_dict(a) for a in await att.for_org(org_id, agent_id or None, scope)]


@router.get("/attachments/{att_id}")
async def get_file_info(att_id: str) -> dict[str, Any]:
    a = await att.get(att_id)
    if a is None:
        raise HTTPException(404, "file not found")
    return att.to_dict(a)


@router.get("/attachments/{att_id}/text")
async def get_text(att_id: str, offset: int = 0, limit: int = 20000) -> dict[str, Any]:
    a = await att.get(att_id)
    if a is None:
        raise HTTPException(404, "file not found")
    text, total = att.read_text(a, offset, limit)
    return {"text": text, "total": total, "offset": offset}


@router.get("/attachments/{att_id}/file")
async def download(att_id: str, download: bool = False) -> FileResponse:
    a = await att.get(att_id)
    path = att.file_path(a) if a else None
    if a is None or path is None or not path.is_file():
        raise HTTPException(404, "file not found")
    # Uploaded content is never trusted: raster images render inline (with the type
    # we verified, not the one the browser claimed); everything else downloads.
    inline = a.kind == "image" and a.mime in att.RASTER and not download
    return FileResponse(
        path, media_type=a.mime if inline else "application/octet-stream", filename=a.name,
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                 "Cache-Control": "private, max-age=3600"})


@router.delete("/attachments/{att_id}", status_code=204)
async def delete_file(att_id: str) -> None:
    try:
        await att.delete(att_id)
    except att.AttachmentError as e:
        raise HTTPException(404, str(e)) from None
