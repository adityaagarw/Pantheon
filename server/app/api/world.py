"""The physical world: spaces, objects, assets and plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app import plugins
from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, Asset, Org, WorldObject
from app.services import assets, spatial
from app.tools.world import object_dict

router = APIRouter(prefix="/api/v1", tags=["world"])


async def _org(org_id: str) -> Org:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "organization not found")
    return org


@router.get("/orgs/{org_id}/world")
async def get_world(org_id: str) -> dict[str, Any]:
    await _org(org_id)
    w = await spatial.load_world(org_id)
    return {
        "enabled": spatial.world_enabled(w.org),
        "rooms": [{"name": r.name, "type": r.type,
                   "items": [{"id": i.id, "kind": i.kind, "label": i.label} for i in r.items]}
                  for r in w.space.rooms],
        "locations": {a.id: a.location for a in w.agents if a.location},
        "objects": [object_dict(o) for o in w.objects],
    }


@router.post("/agents/{agent_id}/move")
async def move_agent(agent_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Direct an agent somewhere (or back to its desk with place="desk")."""
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    w = await spatial.load_world(agent.org_id)
    me = w.by_id.get(agent.id, agent)
    try:
        loc = spatial.resolve_place(str(body.get("place") or ""), w.space, w.agents, me)
    except spatial.PlaceError as e:
        raise HTTPException(400, str(e)) from None
    await spatial.set_location(me, loc, "is sent to " + str(body.get("place")))
    return {"location": loc, "where": w.where(me)}


@router.post("/orgs/{org_id}/world/objects", status_code=201)
async def create_object(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _org(org_id)
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name is required")
    o = WorldObject(org_id=org_id, name=name[:80], asset=str(body.get("asset") or ""),
                    description=str(body.get("description") or ""),
                    holder_id=body.get("holderId") or None,
                    place=body.get("place") or {}, state=body.get("state") or {})
    async with SessionLocal() as session:
        session.add(o)
        await session.commit()
    await bus.publish("world.object", object_dict(o), org_id=org_id)
    return object_dict(o)


@router.delete("/orgs/{org_id}/world/objects/{object_id}", status_code=204)
async def delete_object(org_id: str, object_id: str) -> None:
    async with SessionLocal() as session:
        o = (await session.execute(select(WorldObject).where(
            WorldObject.org_id == org_id, WorldObject.id == object_id))).scalar_one_or_none()
        if o is None:
            raise HTTPException(404, "object not found")
        await session.delete(o)
        await session.commit()
    await bus.publish("world.object.removed", {"id": object_id}, org_id=org_id)


# --- assets ------------------------------------------------------------------------------


@router.get("/assets")
async def list_assets() -> list[dict[str, Any]]:
    return await assets.all_assets()


@router.post("/assets", status_code=201)
async def create_asset(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        if body.get("url"):
            a = await assets.import_glb(
                str(body["url"]), str(body.get("label") or "Asset"), size_m=body.get("sizeM"),
                height_m=body.get("heightM"), category=str(body.get("category") or "Props"),
                carryable=bool(body.get("carryable")), license_=str(body.get("license") or ""),
                description=str(body.get("description") or ""))
        else:
            a = await assets.create_procedural(
                str(body.get("label") or "Asset"), body.get("parts"),
                category=str(body.get("category") or "Props"),
                carryable=bool(body.get("carryable")),
                description=str(body.get("description") or ""))
    except assets.AssetError as e:
        raise HTTPException(400, str(e)) from None
    return assets.to_dict(a)


@router.get("/assets/{asset_id}/file")
async def asset_file(asset_id: str) -> FileResponse:
    async with SessionLocal() as session:
        a = await session.get(Asset, asset_id)
    if a is None or not a.file:
        raise HTTPException(404, "asset file not found")
    path = settings.assets_path / a.file
    if not path.is_file():
        raise HTTPException(404, "asset file missing on disk")
    return FileResponse(path, media_type="model/gltf-binary")


@router.delete("/assets/{asset_id}", status_code=204)
async def delete_asset(asset_id: str) -> None:
    try:
        await assets.delete_asset(asset_id)
    except assets.AssetError as e:
        raise HTTPException(404, str(e)) from None


# --- plugins -----------------------------------------------------------------------------


@router.get("/plugins")
async def list_plugins() -> dict[str, Any]:
    return {"dir": str(plugins.plugins_dir()),
            "plugins": [p.info() for p in plugins.all_plugins()]}


@router.get("/plugins/{plugin_id}/files/{path:path}")
async def plugin_file(plugin_id: str, path: str) -> FileResponse:
    p = plugins.get(plugin_id)
    if p is None:
        raise HTTPException(404, "plugin not found")
    root = p.path.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    media = "model/gltf-binary" if target.suffix == ".glb" else None
    return FileResponse(Path(target), media_type=media)
