"""The Stage, the agents' live browsers, the computer, and agents' schedules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.browser.manager import manager as browsers
from app.core.db import SessionLocal
from app.models import Agent
from app.services import schedules, sitepack, stage

router = APIRouter(prefix="/api/v1", tags=["stage"])

# Pages run in an opaque origin even if opened directly: agent-written scripts can
# never use the viewer's access to Pantheon. The library is fetched cross-origin.
SANDBOX = {"Content-Security-Policy": "sandbox allow-scripts allow-popups allow-forms",
           "Cache-Control": "no-store"}
# Revalidated, so a library fix reaches open lessons on reload.
LIB_HEADERS = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"}
TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
         ".woff2": "font/woff2"}


@router.get("/stage-lib/{path:path}")
async def stage_lib(path: str) -> FileResponse:
    f = stage.lib_file(path)
    if f is None:
        raise HTTPException(404, "not found")
    return FileResponse(f, media_type=TYPES.get(f.suffix, "application/octet-stream"),
                        headers=LIB_HEADERS)


@router.get("/stage/{page_id}/view", response_class=HTMLResponse)
async def stage_view(page_id: str) -> HTMLResponse:
    p = await stage.get(page_id)
    if p is None:
        raise HTTPException(404, "page not found")
    return HTMLResponse(stage.render(p, await stage.libraries_for(p.org_id, p.agent_id)),
                        headers=SANDBOX)


@router.get("/sites/{org_id}/{path:path}")
async def site(org_id: str, path: str) -> Response:
    """A packaged Stage site (stage_package). Its pages are sandboxed like Stage pages."""
    if path and "/" not in path and not path.endswith(".zip"):
        return RedirectResponse(f"{path}/")  # relative links need the trailing slash
    f = sitepack.site_file(org_id, path + "index.html" if path.endswith("/") else path)
    if f is None:
        raise HTTPException(404, "not found")
    if f.suffix == ".html":
        return FileResponse(f, media_type="text/html", headers=SANDBOX)
    kind = {".zip": "application/zip", ".json": "application/json"}.get(f.suffix)
    return FileResponse(f, media_type=kind or TYPES.get(f.suffix, "application/octet-stream"),
                        headers=LIB_HEADERS)


@router.get("/orgs/{org_id}/stage")
async def stage_pages(org_id: str) -> list[dict[str, Any]]:
    return [stage.to_dict(p) for p in await stage.for_org(org_id)]


@router.get("/stage/{page_id}")
async def stage_page(page_id: str) -> dict[str, Any]:
    p = await stage.get(page_id)
    if p is None:
        raise HTTPException(404, "page not found")
    return stage.to_dict(p, source=True)


@router.patch("/stage/{page_id}")
async def move_stage_page(page_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """{"folder": "archive"} archives a page; {"folder": ""} puts it back on the list."""
    try:
        return stage.to_dict(await stage.move(page_id, str(body.get("folder") or "")))
    except stage.StageError as e:
        raise HTTPException(404 if "not found" in str(e) else 400, str(e)) from None


@router.delete("/stage/{page_id}", status_code=204)
async def delete_stage_page(page_id: str) -> None:
    try:
        await stage.delete(page_id)
    except stage.StageError as e:
        raise HTTPException(404, str(e)) from None


@router.post("/agents/{agent_id}/attention", status_code=204)
async def agent_attention(agent_id: str, body: dict[str, Any] = Body(...)) -> None:
    """The Stage UI reports what the user is looking at (for teach-along)."""
    kind = str(body.get("kind") or "page")
    if kind == "none":
        stage.clear_attention(agent_id)
        return
    async with SessionLocal() as session:
        if await session.get(Agent, agent_id) is None:
            raise HTTPException(404, "agent not found")
    if kind == "page":
        page = await stage.get(str(body.get("pageId") or ""))
        if page is None:
            raise HTTPException(404, "page not found")
        stage.set_attention(agent_id, {"kind": "page", "pageId": page.id, "title": page.title,
                                       "text": str(body.get("text") or "")[:300],
                                       "held": bool(body.get("held"))})
    elif kind == "browser":
        stage.set_attention(agent_id, {"kind": "browser",
                                       "title": str(body.get("title") or "")[:200],
                                       "url": str(body.get("url") or "")[:500]})
    elif kind == "whiteboard":
        stage.set_attention(agent_id, {"kind": "whiteboard",
                                       "title": str(body.get("title") or "")[:200]})
    elif kind in ("sheet", "pdf", "notebook", "markdown"):
        stage.set_attention(agent_id, {"kind": kind,
                                       "title": str(body.get("title") or "")[:200],
                                       "docId": str(body.get("docId") or "")[:60],
                                       "text": str(body.get("text") or "")[:300]})
    else:
        raise HTTPException(400, f"unknown attention kind '{kind}'")


@router.get("/agents/{agent_id}/browser")
async def agent_browser(agent_id: str) -> dict[str, Any]:
    s = browsers.latest(agent_id)
    return {"open": s is not None and bool(s.shot), "url": s.url if s else "",
            "title": s.title if s else ""}


@router.get("/agents/{agent_id}/browser.jpg")
async def agent_browser_shot(agent_id: str) -> Response:
    s = browsers.latest(agent_id)
    if s is None or not s.shot:
        raise HTTPException(404, "no browser open")
    return Response(s.shot, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/agents/{agent_id}/schedules")
async def agent_schedules(agent_id: str) -> list[dict[str, Any]]:
    return [schedules.to_dict(s) for s in await schedules.for_agent(agent_id)]


@router.delete("/schedules/{schedule_id}", status_code=204)
async def cancel_schedule(schedule_id: str) -> None:
    try:
        await schedules.cancel(schedule_id)
    except schedules.ScheduleError as e:
        raise HTTPException(404, str(e)) from None
