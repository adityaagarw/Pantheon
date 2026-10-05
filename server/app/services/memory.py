"""Long-term memory: private per-agent notes plus org-wide shared knowledge.

Search is hybrid: semantic similarity (pgvector over local embeddings) fused
with Postgres full-text ranking, so both "what does the user like?" and exact
names/numbers find the right notes. Without embeddings it's keyword-only.

Near-duplicate notes are merged instead of piling up, and the runtime pulls
the few most relevant memories into each turn automatically (``relevant``).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import func, or_, select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Memory
from app.services import embeddings

log = logging.getLogger(__name__)
DUPLICATE = 0.95  # cosine similarity above which a new note updates the old one
RELEVANT = 0.62  # auto-recall threshold (bge-small scale)
RRF_K = 60


def to_dict(m: Memory, score: float | None = None) -> dict[str, Any]:
    out = {"id": m.id, "orgId": m.org_id, "agentId": m.agent_id, "kind": m.kind,
           "content": m.content, "source": m.source, "shared": m.agent_id is None,
           "createdAt": m.created_at.isoformat() if m.created_at else None,
           "updatedAt": m.updated_at.isoformat() if m.updated_at else None}
    if score is not None:
        out["score"] = round(score, 3)
    return out


ALL = "*"  # every memory in the org (for the user's Memory view)


def _scope(agent_id: str | None, include_shared: bool = True):
    if agent_id == ALL:
        return Memory.id.is_not(None)
    if agent_id is None:
        return Memory.agent_id.is_(None)
    if include_shared:
        return or_(Memory.agent_id.is_(None), Memory.agent_id == agent_id)
    return Memory.agent_id == agent_id


async def add(org_id: str, content: str, *, agent_id: str | None, kind: str = "note",
              source: str = "agent") -> Memory:
    from app.services import secrets

    content = secrets.redact(org_id, content.strip())
    vec = (await embeddings.embed([content]) or [None])[0]
    async with SessionLocal() as session:
        if vec is not None:
            near = (await session.execute(
                select(Memory, Memory.embedding.cosine_distance(vec).label("d")).where(
                    Memory.org_id == org_id, _scope(agent_id, include_shared=False)
                    if agent_id else Memory.agent_id.is_(None),
                    Memory.embedding.is_not(None))
                .order_by("d").limit(1))).first()
            if near is not None and 1 - float(near.d) >= DUPLICATE:
                m = near.Memory
                m.content, m.embedding = content, vec
                await session.commit()
                await bus.publish("memory.saved", to_dict(m), org_id=org_id, agent_id=agent_id)
                return m
        m = Memory(org_id=org_id, agent_id=agent_id, kind=kind, content=content,
                   embedding=vec, source=source)
        session.add(m)
        await session.commit()
    await bus.publish("memory.saved", to_dict(m), org_id=org_id, agent_id=agent_id)
    return m


async def search(org_id: str, query: str, *, agent_id: str | None, k: int = 8,
                 include_shared: bool = True) -> list[Memory]:
    return [m for m, _ in await scored_search(org_id, query, agent_id=agent_id, k=k,
                                              include_shared=include_shared)]


async def scored_search(org_id: str, query: str, *, agent_id: str | None, k: int = 8,
                        include_shared: bool = True, min_similarity: float = 0.45
                        ) -> list[tuple[Memory, float]]:
    """Hybrid (vector + keyword) search; score is the vector similarity when known."""
    scope = _scope(agent_id, include_shared)
    async with SessionLocal() as session:
        if not query.strip():
            rows = (await session.execute(select(Memory).where(Memory.org_id == org_id, scope)
                                          .order_by(Memory.updated_at.desc()).limit(k)))
            return [(m, 0.0) for m in rows.scalars()]
        ranked: dict[str, list[Any]] = {}  # id -> [memory, rrf, similarity]
        vec = (await embeddings.embed([query]) or [None])[0]
        if vec is not None:
            rows = (await session.execute(
                select(Memory, Memory.embedding.cosine_distance(vec).label("d")).where(
                    Memory.org_id == org_id, scope, Memory.embedding.is_not(None))
                .order_by("d").limit(k * 3))).all()
            for rank, r in enumerate(rows):
                sim = 1 - float(r.d)
                if sim >= min_similarity:
                    ranked[r.Memory.id] = [r.Memory, 1 / (RRF_K + rank), sim]
        doc = func.to_tsvector("english", Memory.content)
        q = func.websearch_to_tsquery("english", query)
        rows = (await session.execute(
            select(Memory).where(Memory.org_id == org_id, scope, doc.op("@@")(q))
            .order_by(func.ts_rank(doc, q).desc(), Memory.created_at.desc()).limit(k * 3)
        )).scalars().all()
        if not rows and not ranked:
            terms = [t for t in query.split() if len(t) > 2][:5]
            if terms:
                rows = (await session.execute(
                    select(Memory).where(Memory.org_id == org_id, scope,
                                         or_(*[Memory.content.ilike(f"%{t}%") for t in terms]))
                    .order_by(Memory.created_at.desc()).limit(k))).scalars().all()
        for rank, m in enumerate(rows):
            entry = ranked.setdefault(m.id, [m, 0.0, 0.0])
            entry[1] += 1 / (RRF_K + rank)
    best = sorted(ranked.values(), key=lambda e: e[1], reverse=True)[:k]
    return [(m, sim) for m, _, sim in best]


async def relevant(org_id: str, agent_id: str, text: str, k: int = 5) -> list[Memory]:
    """Memories worth bringing into a turn about ``text`` (semantic only, thresholded)."""
    if not text.strip() or not embeddings.enabled():
        return []
    hits = await scored_search(org_id, text[:2000], agent_id=agent_id, k=k,
                               min_similarity=RELEVANT)
    return [m for m, sim in hits if sim >= RELEVANT]


async def update(memory_id: str, content: str) -> Memory:
    vec = (await embeddings.embed([content.strip()]) or [None])[0]
    async with SessionLocal() as session:
        m = await session.get(Memory, memory_id)
        if m is None:
            raise ValueError("memory not found")
        m.content, m.embedding = content.strip(), vec
        await session.commit()
    await bus.publish("memory.saved", to_dict(m), org_id=m.org_id, agent_id=m.agent_id)
    return m


async def delete(memory_id: str) -> None:
    async with SessionLocal() as session:
        m = await session.get(Memory, memory_id)
        if m is None:
            raise ValueError("memory not found")
        await session.delete(m)
        await session.commit()
    await bus.publish("memory.deleted", {"id": memory_id}, org_id=m.org_id)


async def backfill(batch: int = 64) -> int:
    """Embed memories saved before semantic search existed (runs at startup)."""
    done = 0
    while embeddings.enabled():
        async with SessionLocal() as session:
            rows = (await session.execute(select(Memory).where(Memory.embedding.is_(None))
                                          .limit(batch))).scalars().all()
            if not rows:
                break
            vecs = await embeddings.embed([m.content for m in rows])
            if vecs is None:
                break
            for m, v in zip(rows, vecs, strict=True):
                m.embedding = v
            await session.commit()
            done += len(rows)
        await asyncio.sleep(0)
    if done:
        log.info("embedded %d existing memories", done)
    return done
