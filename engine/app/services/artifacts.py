"""Artifacts: files agents produced in the workspace (indexed for the UI)."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Artifact

if TYPE_CHECKING:
    from app.tools.base import ToolContext


def artifact_to_dict(a: Artifact) -> dict[str, Any]:
    return {"id": a.id, "orgId": a.org_id, "agentId": a.agent_id, "taskId": a.task_id,
            "path": a.path, "title": a.title, "mime": a.mime, "size": a.size,
            "createdAt": a.created_at.isoformat() if a.created_at else None}


async def record(ctx: ToolContext, path: Path) -> Artifact:
    rel = str(path.relative_to(ctx.workspace)).replace("\\", "/")
    mime = mimetypes.guess_type(path.name)[0] or "text/plain"
    size = path.stat().st_size
    async with SessionLocal() as session:
        art = (await session.execute(select(Artifact).where(
            Artifact.org_id == ctx.org.id, Artifact.path == rel))).scalar_one_or_none()
        if art is None:
            art = Artifact(org_id=ctx.org.id, agent_id=ctx.agent.id, path=rel, title=path.name,
                           mime=mime, size=size)
            session.add(art)
        else:
            art.agent_id, art.size, art.mime = ctx.agent.id, size, mime
        await session.commit()
    await bus.publish("artifact.updated", artifact_to_dict(art), org_id=ctx.org.id,
                      agent_id=ctx.agent.id)
    return art
