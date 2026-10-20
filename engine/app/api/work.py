"""Boards, tasks and artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Artifact, Board, Org, Task
from app.services import tasks
from app.services.artifacts import artifact_to_dict
from app.services.comms import Sender, message_to_dict
from app.services.tasks import board_to_dict, task_to_dict
from app.tools.base import org_workspace

router = APIRouter(prefix="/api/v1", tags=["work"])


@router.get("/orgs/{org_id}/boards")
async def list_boards(org_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        await tasks.ensure_default_board(session, org_id)
        await session.commit()
        rows = (await session.execute(select(Board).where(Board.org_id == org_id)
                                      .order_by(Board.created_at))).scalars().all()
    return [board_to_dict(b) for b in rows]


@router.post("/orgs/{org_id}/boards", status_code=201)
async def create_board(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "name is required")
    cols = body.get("columns") or tasks.DEFAULT_COLUMNS
    for c in cols:
        if c.get("key") not in tasks.STATUSES:
            raise HTTPException(400, f"column key must be one of {tasks.STATUSES}")
    async with SessionLocal() as session:
        b = Board(org_id=org_id, name=name, description=body.get("description", ""),
                  columns=cols)
        session.add(b)
        await session.commit()
    return board_to_dict(b)


@router.patch("/boards/{board_id}")
async def update_board(board_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with SessionLocal() as session:
        b = await session.get(Board, board_id)
        if b is None:
            raise HTTPException(404, "board not found")
        if body.get("name"):
            b.name = body["name"]
        if "description" in body:
            b.description = body["description"] or ""
        if body.get("columns"):
            for c in body["columns"]:
                if c.get("key") not in tasks.STATUSES:
                    raise HTTPException(400, f"column key must be one of {tasks.STATUSES}")
            b.columns = body["columns"]
        await session.commit()
    return board_to_dict(b)


@router.get("/orgs/{org_id}/tasks")
async def list_tasks(org_id: str, board_id: str | None = None, assignee: str | None = None,
                     include_closed: bool = True) -> list[dict[str, Any]]:
    rows = await tasks.list_tasks(org_id, board_id=board_id, assignee=assignee,
                                  include_closed=include_closed, limit=2000)
    return [task_to_dict(t) for t in rows]


@router.post("/orgs/{org_id}/tasks", status_code=201)
async def create_task(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    t = await tasks.create_task(
        org_id, Sender.user(), title=body.get("title", ""),
        description=body.get("description", ""), acceptance=body.get("acceptance", ""),
        assignee=body.get("assigneeId"), reviewer=body.get("reviewerId"),
        priority=body.get("priority", "normal"), status=body.get("status"),
        board_id=body.get("boardId"), parent=body.get("parentId"),
        depends_on=body.get("dependsOn"), labels=body.get("labels"),
    )
    return task_to_dict(t)


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    from app.api.comms import org_messages

    return task_to_dict(t) | {
        "timeline": await tasks.task_timeline(t.org_id, t.id),
        "messages": await org_messages(t.org_id, task_id=t.id, limit=500),
    }


@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    async with SessionLocal() as session:
        t = await session.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    mapping = {"assigneeId": "assignee", "reviewerId": "reviewer", "boardId": "board_id",
               "dependsOn": "depends_on"}
    fields = {mapping.get(k, k): v for k, v in body.items()
              if mapping.get(k, k) in {"title", "description", "acceptance", "status", "assignee",
                                       "unassign", "reviewer", "priority", "result", "board_id",
                                       "position", "depends_on", "labels", "comment"}}
    if fields.get("assignee") is None and "assignee" in fields:
        fields.pop("assignee")
        fields["unassign"] = True
    t = await tasks.update_task(t.org_id, Sender.user(), t.id, **fields)
    return task_to_dict(t)


@router.get("/orgs/{org_id}/artifacts")
async def list_artifacts(org_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(Artifact).where(Artifact.org_id == org_id)
                                      .order_by(Artifact.created_at.desc()))).scalars().all()
    return [artifact_to_dict(a) for a in rows]


async def _workspace(org_id: str) -> Path:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
    if org is None:
        raise HTTPException(404, "org not found")
    return org_workspace(org)


@router.get("/orgs/{org_id}/files")
async def list_files(org_id: str, path: str = "") -> dict[str, Any]:
    root = await _workspace(org_id)
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.exists():
        raise HTTPException(404, "not found")
    if target.is_file():
        return {"path": path, "type": "file", "size": target.stat().st_size}
    entries = []
    for c in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        if c.name in (".git", "node_modules", ".venv", "__pycache__"):
            continue
        entries.append({"name": c.name, "type": "dir" if c.is_dir() else "file",
                        "size": c.stat().st_size if c.is_file() else None,
                        "path": str(c.relative_to(root)).replace("\\", "/")})
    return {"path": path, "type": "dir", "root": str(root), "entries": entries}


@router.get("/orgs/{org_id}/files/raw")
async def raw_file(org_id: str, path: str) -> FileResponse:
    root = await _workspace(org_id)
    target = (root / path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(target)


_ = message_to_dict
