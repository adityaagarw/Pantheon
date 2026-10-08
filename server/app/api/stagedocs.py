"""Stage documents: spreadsheets, PDFs and notebooks shared by the user and agents."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

from app.services import attachments as att
from app.services import notebook_kernel
from app.services import stagedocs as docs
from app.services.stagedocs import DocError

router = APIRouter(prefix="/api/v1", tags=["stage-docs"])
_runs: set[asyncio.Task] = set()


def _bad(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


async def _doc(doc_id: str, kind: str | None = None):
    try:
        d = await docs.get(doc_id)
    except DocError as e:
        raise HTTPException(404, str(e)) from None
    if kind and d.kind != kind:
        raise HTTPException(400, f"this is a {d.kind}, not a {kind}")
    return d


@router.get("/orgs/{org_id}/docs")
async def list_docs(org_id: str) -> list[dict[str, Any]]:
    return [docs.to_dict(d) for d in await docs.for_org(org_id)]


@router.post("/orgs/{org_id}/docs", status_code=201)
async def create_doc(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """{kind, title?, attachmentId?}: a new sheet or notebook, or one made from a file
    (.xlsx/.csv -> sheet, .ipynb -> notebook, .pdf -> pdf)."""
    kind, title = str(body.get("kind", "")), str(body.get("title") or "")
    att_id = body.get("attachmentId")
    try:
        if att_id:
            a = await att.get(str(att_id))
            if a is None or a.org_id != org_id:
                raise DocError("file not found")
            if kind == "pdf":
                d = await docs.open_pdf(org_id, a, title or None, created_by="user",
                                        by_name="user")
            else:
                raw = att.file_path(a).read_bytes()
                data = ({"cells": docs.notebook_from_ipynb(raw)["cells"]} if kind == "notebook"
                        else {"sheets": docs.sheets_from_file(a.name, raw)})
                d = await docs.create(org_id, kind, title or a.name.rsplit(".", 1)[0], data)
        elif kind == "pdf":
            raise DocError("open a PDF from Files")
        else:
            d = await docs.create(org_id, kind, title)
    except DocError as e:
        raise _bad(e) from None
    return docs.to_dict(d, full=True)


@router.get("/docs/{doc_id}")
async def get_doc(doc_id: str) -> dict[str, Any]:
    return docs.to_dict(await _doc(doc_id), full=True)


@router.patch("/docs/{doc_id}")
async def rename_doc(doc_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return docs.to_dict(await docs.rename(doc_id, str(body.get("title") or "")))
    except DocError as e:
        raise _bad(e) from None


@router.delete("/docs/{doc_id}", status_code=204)
async def delete_doc(doc_id: str) -> None:
    await _doc(doc_id)
    await docs.delete_doc(doc_id)


@router.get("/docs/{doc_id}/export")
async def export_doc(doc_id: str) -> Response:
    d = await _doc(doc_id)
    safe = "".join(ch for ch in d.title if ch.isalnum() or ch in " -_").strip() or "document"
    if d.kind == "sheet":
        return Response(docs.sheet_xlsx(d), headers={
            "Content-Disposition": f'attachment; filename="{safe}.xlsx"'},
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if d.kind == "notebook":
        return Response(docs.notebook_ipynb(d), media_type="application/x-ipynb+json",
                        headers={"Content-Disposition": f'attachment; filename="{safe}.ipynb"'})
    a = await att.get((d.data or {}).get("attachmentId", ""))
    if a is None or not att.file_path(a).is_file():
        raise HTTPException(404, "the PDF file is gone")
    return FileResponse(att.file_path(a), media_type="application/pdf", filename=a.name,
                        headers={"X-Content-Type-Options": "nosniff",
                                 "Content-Security-Policy": "sandbox"})


# --- spreadsheets ---------------------------------------------------------------------------


@router.put("/docs/{doc_id}/cells")
async def save_cells(doc_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The open spreadsheet saves the user's edits: {changes: {sheet: {ref: cell|null}},
    sheetNames?: [...]}."""
    await _doc(doc_id, "sheet")
    changes = body.get("changes")
    if not isinstance(changes, dict):
        raise HTTPException(400, "changes must be an object")
    try:
        d = await docs.apply_user_changes(doc_id, changes, body.get("sheetNames"))
    except (DocError, ValueError) as e:
        raise _bad(e) from None
    return {"version": d.version}


# --- PDFs -----------------------------------------------------------------------------------


@router.post("/docs/{doc_id}/pdf")
async def pdf_view(doc_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _doc(doc_id, "pdf")
    try:
        d = await docs.show_pdf(doc_id, body.get("page"), body.get("highlight"),
                                body.get("note"), "user", clear=bool(body.get("clear")))
    except DocError as e:
        raise _bad(e) from None
    return docs.to_dict(d, full=True)


# --- notebooks ------------------------------------------------------------------------------


@router.post("/docs/{doc_id}/cells", status_code=201)
async def add_cell(doc_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _doc(doc_id, "notebook")
    try:
        return await docs.add_cell(doc_id, str(body.get("source") or ""),
                                   str(body.get("type") or "code"), "user", body.get("after"))
    except DocError as e:
        raise _bad(e) from None


@router.patch("/docs/{doc_id}/cells/{cell_id}")
async def edit_cell(doc_id: str, cell_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    await _doc(doc_id, "notebook")
    try:
        if "to" in body:
            await docs.move_cell(doc_id, cell_id, int(body["to"]), "user")
            return {"ok": True}
        return await docs.edit_cell(doc_id, cell_id, source=body.get("source"),
                                    kind=body.get("type"), by_name="user")
    except DocError as e:
        raise _bad(e) from None


@router.delete("/docs/{doc_id}/cells/{cell_id}", status_code=204)
async def delete_cell(doc_id: str, cell_id: str) -> None:
    await _doc(doc_id, "notebook")
    try:
        await docs.delete_cell(doc_id, cell_id, "user")
    except DocError as e:
        raise _bad(e) from None


def _background(coro) -> None:
    task = asyncio.create_task(coro)
    _runs.add(task)
    task.add_done_callback(_runs.discard)


@router.post("/docs/{doc_id}/cells/{cell_id}/run", status_code=202)
async def run_cell(doc_id: str, cell_id: str) -> dict[str, Any]:
    """Starts the cell; its output arrives as stage_doc.updated."""
    await _doc(doc_id, "notebook")
    _background(docs.run_cell(doc_id, cell_id, "user"))
    return {"started": True}


@router.post("/docs/{doc_id}/run-all", status_code=202)
async def run_all(doc_id: str) -> dict[str, Any]:
    await _doc(doc_id, "notebook")
    _background(docs.run_all(doc_id, "user"))
    return {"started": True}


@router.post("/docs/{doc_id}/kernel/{action}")
async def kernel(doc_id: str, action: str) -> dict[str, Any]:
    await _doc(doc_id, "notebook")
    try:
        if action == "interrupt":
            await notebook_kernel.interrupt(doc_id)
        elif action == "restart":
            await notebook_kernel.restart(doc_id)
        else:
            raise HTTPException(404, "unknown kernel action")
    except notebook_kernel.KernelError as e:
        raise _bad(e) from None
    return {"ok": True}
