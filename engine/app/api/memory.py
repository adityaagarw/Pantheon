"""Browse, search, edit and add agents' long-term memories."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Agent, Memory, Org
from app.services import embeddings, memory

router = APIRouter(prefix="/api/v1", tags=["memory"])


@router.get("/orgs/{org_id}/memories")
async def list_memories(org_id: str, q: str = "", agent: str = "", scope: str = "all",
                        limit: int = 100) -> dict[str, Any]:
    """scope: all | shared | agent (with agent=<id>: that agent's private notes)."""
    limit = max(1, min(limit, 500))
    if q.strip():
        agent_id = agent or (memory.ALL if scope == "all" else None)
        hits = await memory.scored_search(org_id, q, agent_id=agent_id, k=limit,
                                          include_shared=scope != "agent")
        if scope == "shared":
            hits = [(m, s) for m, s in hits if m.agent_id is None]
        items = [memory.to_dict(m, s) for m, s in hits]
    else:
        async with SessionLocal() as session:
            stmt = select(Memory).where(Memory.org_id == org_id)
            if scope == "shared":
                stmt = stmt.where(Memory.agent_id.is_(None))
            elif agent:
                stmt = stmt.where(Memory.agent_id == agent)
            rows = (await session.execute(stmt.order_by(Memory.updated_at.desc())
                                          .limit(limit))).scalars().all()
        items = [memory.to_dict(m) for m in rows]
    async with SessionLocal() as session:
        counts = dict((await session.execute(select(Memory.agent_id, func.count()).where(
            Memory.org_id == org_id).group_by(Memory.agent_id))).all())
    return {"items": items, "counts": {k or "shared": v for k, v in counts.items()},
            "embeddings": embeddings.status()}


@router.post("/orgs/{org_id}/memories", status_code=201)
async def add_memory(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    agent_id = body.get("agentId") or None
    async with SessionLocal() as session:
        if await session.get(Org, org_id) is None:
            raise HTTPException(404, "organization not found")
        if agent_id and await session.get(Agent, agent_id) is None:
            raise HTTPException(404, "agent not found")
    m = await memory.add(org_id, content, agent_id=agent_id, source="user")
    return memory.to_dict(m)


@router.patch("/memories/{memory_id}")
async def edit_memory(memory_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    content = str(body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, "content is required")
    try:
        return memory.to_dict(await memory.update(memory_id, content))
    except ValueError as e:
        raise HTTPException(404, str(e)) from None


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: str) -> None:
    try:
        await memory.delete(memory_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
