"""Packs: list, switch on and off, add (a folder, a zip, or an npm library) and remove."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app import packs
from app.services import npmpack

router = APIRouter(prefix="/api/v1", tags=["packs"])

MAX_ZIP = 200_000_000


def _listing() -> dict[str, Any]:
    # The shipped packs first, then the user's own.
    ordered = sorted(packs.all_packs(), key=lambda p: (not p.bundled, p.name.lower()))
    return {"dir": str(packs.packs_dir()),
            "packs": [p.info() for p in ordered],
            "libraries": [lib.info() for lib in packs.libraries().values()]}


@router.get("/packs")
async def list_packs() -> dict[str, Any]:
    return _listing()


@router.post("/packs/reload")
async def reload_packs() -> dict[str, Any]:
    """Pick up packs added or changed by hand in the packs folder."""
    packs.load_all(force=True)
    return _listing()


@router.patch("/packs/{pack_id}")
async def update_pack(pack_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    if "enabled" not in body:
        raise HTTPException(400, "nothing to change (enabled: true or false)")
    try:
        pack = await packs.set_enabled(pack_id, bool(body["enabled"]))
    except packs.PackError as e:
        raise HTTPException(400, str(e)) from None
    return pack.info()


@router.delete("/packs/{pack_id}", status_code=204)
async def remove_pack(pack_id: str) -> None:
    try:
        await asyncio.to_thread(packs.remove, pack_id)
    except packs.PackError as e:
        raise HTTPException(400, str(e)) from None


@router.post("/packs/upload")
async def upload_pack(file: UploadFile = File(...)) -> dict[str, Any]:
    """A pack as a .zip (pack.json at its top or in one folder inside)."""
    data = await file.read(MAX_ZIP + 1)
    if len(data) > MAX_ZIP:
        raise HTTPException(413, "the zip is too large")
    try:
        pack = await asyncio.to_thread(packs.install_zip, data)
    except (packs.PackError, ValueError) as e:
        raise HTTPException(400, str(e)) from None
    return pack.info()


@router.post("/packs/folder")
async def add_folder(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """A pack folder on this computer, copied into the packs folder."""
    src = Path(str(body.get("path") or "")).expanduser()
    if not src.is_dir():
        raise HTTPException(400, f"no folder {src}")
    try:
        pack = await asyncio.to_thread(packs.install_folder, src)
    except packs.PackError as e:
        raise HTTPException(400, str(e)) from None
    return pack.info()


@router.post("/packs/npm")
async def add_npm(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """An npm library as a Stage library pack: {"spec": "chart.js@4"}."""
    try:
        pack, warnings = await npmpack.install(str(body.get("spec") or ""))
    except (npmpack.NpmError, packs.PackError) as e:
        raise HTTPException(400, str(e)) from None
    return {**pack.info(), "warnings": warnings}


@router.get("/packs/libraries/{lib_id}/docs")
async def library_docs(lib_id: str) -> dict[str, Any]:
    lib = packs.libraries().get(lib_id)
    if lib is None:
        raise HTTPException(404, "no such library")
    return {**lib.info(), "docs": lib.docs}


@router.get("/office")
async def office() -> dict[str, Any]:
    """The office content of every enabled pack (furniture, models, characters, themes),
    shipped packs first; files are under each pack's ``base``."""
    return {"packs": [{"id": p.id, "name": p.name, "base": f"/api/v1/packs/{p.id}/files", **o}
                      for p, o in packs.office_packs()]}


@router.get("/packs/{pack_id}/files/{path:path}")
async def pack_file(pack_id: str, path: str) -> FileResponse:
    p = packs.get(pack_id)
    if p is None or not p.usable:
        raise HTTPException(404, "pack not found")
    root = p.path.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not found")
    media = "model/gltf-binary" if target.suffix == ".glb" else None
    # Revalidated (a 304 when unchanged), so an edited pack shows on reload.
    return FileResponse(target, media_type=media, headers={"Cache-Control": "no-cache"})
