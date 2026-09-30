"""Observability: turns (agent sessions), full LLM/tool traces, search, stats, events."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy import Date, String, cast, func, or_, select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Agent, LlmCall, Message, ToolCall, Turn
from app.services.comms import message_to_dict

router = APIRouter(prefix="/api/v1", tags=["observe"])


def turn_to_dict(t: Turn) -> dict[str, Any]:
    return {"id": t.id, "orgId": t.org_id, "agentId": t.agent_id, "status": t.status,
            "triggerMessageIds": t.trigger_message_ids, "steps": t.steps,
            "inputTokens": t.input_tokens, "outputTokens": t.output_tokens,
            "costUsd": t.cost_usd, "summary": t.summary, "error": t.error,
            "attempts": t.attempts,
            "startedAt": t.started_at.isoformat() if t.started_at else None,
            "endedAt": t.ended_at.isoformat() if t.ended_at else None}


def llm_to_dict(c: LlmCall, full: bool = False) -> dict[str, Any]:
    d = {"id": c.id, "turnId": c.turn_id, "agentId": c.agent_id, "purpose": c.purpose,
         "provider": c.provider, "model": c.model, "inputTokens": c.input_tokens,
         "outputTokens": c.output_tokens, "costUsd": c.cost_usd, "latencyMs": c.latency_ms,
         "error": c.error, "createdAt": c.created_at.isoformat() if c.created_at else None,
         "response": c.response}
    if full:
        d["request"] = c.request
    return d


def tool_to_dict(t: ToolCall) -> dict[str, Any]:
    return {"id": t.id, "turnId": t.turn_id, "agentId": t.agent_id, "name": t.name,
            "args": t.args, "status": t.status, "result": t.result,
            "durationMs": t.duration_ms,
            "createdAt": t.created_at.isoformat() if t.created_at else None}


@router.get("/orgs/{org_id}/turns")
async def list_turns(org_id: str, agent_id: str | None = None, status: str | None = None,
                     limit: int = 100, before: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(Turn).where(Turn.org_id == org_id)
        if agent_id:
            stmt = stmt.where(Turn.agent_id == agent_id)
        if status:
            stmt = stmt.where(Turn.status == status)
        if before:
            ref = await session.get(Turn, before)
            if ref is not None:
                stmt = stmt.where(Turn.started_at < ref.started_at)
        rows = (await session.execute(stmt.order_by(Turn.started_at.desc())
                                      .limit(min(limit, 500)))).scalars().all()
    return [turn_to_dict(t) for t in rows]


@router.get("/turns/{turn_id}")
async def get_turn(turn_id: str) -> dict[str, Any]:
    """A turn with everything that happened in it, in order."""
    async with SessionLocal() as session:
        t = await session.get(Turn, turn_id)
        if t is None:
            raise HTTPException(404, "turn not found")
        calls = (await session.execute(select(LlmCall).where(LlmCall.turn_id == turn_id)
                                       .order_by(LlmCall.created_at))).scalars().all()
        tools = (await session.execute(select(ToolCall).where(ToolCall.turn_id == turn_id)
                                       .order_by(ToolCall.created_at))).scalars().all()
        trigger = (await session.execute(select(Message).where(
            Message.id.in_(t.trigger_message_ids or [])).order_by(Message.created_at))
        ).scalars().all()
        sent = (await session.execute(select(Message).where(Message.turn_id == turn_id)
                                      .order_by(Message.created_at))).scalars().all()
    return turn_to_dict(t) | {
        "llmCalls": [llm_to_dict(c) for c in calls],
        "toolCalls": [tool_to_dict(x) for x in tools],
        "inbox": [message_to_dict(m) for m in trigger],
        "sent": [message_to_dict(m) for m in sent],
    }


@router.get("/messages/{message_id}/thinking")
async def message_thinking(message_id: str) -> dict[str, Any]:
    """What the agent was thinking in the turn that produced this message, up to sending it."""
    async with SessionLocal() as session:
        m = await session.get(Message, message_id)
        if m is None:
            raise HTTPException(404, "message not found")
        if not m.turn_id:
            return {"turnId": None, "steps": []}
        calls = (await session.execute(select(LlmCall).where(
            LlmCall.turn_id == m.turn_id, LlmCall.purpose == "turn",
            LlmCall.created_at <= m.created_at).order_by(LlmCall.created_at))).scalars().all()
    steps = []
    for c in calls:
        r = c.response or {}
        steps.append({"reasoning": r.get("reasoning") or "", "text": r.get("content") or "",
                      "tools": [t.get("name") for t in r.get("tool_calls") or []],
                      "at": c.created_at.isoformat() if c.created_at else None})
    return {"turnId": m.turn_id, "steps": steps}


@router.get("/llm-calls/{call_id}")
async def get_llm_call(call_id: str) -> dict[str, Any]:
    async with SessionLocal() as session:
        c = await session.get(LlmCall, call_id)
    if c is None:
        raise HTTPException(404, "call not found")
    return llm_to_dict(c, full=True)


@router.get("/orgs/{org_id}/search")
async def search(org_id: str, q: str, limit: int = 50) -> dict[str, Any]:
    q = q.strip()
    if len(q) < 2:
        raise HTTPException(400, "query too short")
    pat = f"%{q}%"
    async with SessionLocal() as session:
        msgs = (await session.execute(select(Message).where(
            Message.org_id == org_id, Message.content.ilike(pat))
            .order_by(Message.created_at.desc()).limit(limit))).scalars().all()
        turns = (await session.execute(select(Turn).where(
            Turn.org_id == org_id, or_(Turn.summary.ilike(pat), Turn.error.ilike(pat)))
            .order_by(Turn.started_at.desc()).limit(limit))).scalars().all()
        tools = (await session.execute(select(ToolCall).where(
            ToolCall.org_id == org_id,
            or_(ToolCall.result.ilike(pat), cast(ToolCall.args, String).ilike(pat),
                ToolCall.name.ilike(pat)))
            .order_by(ToolCall.created_at.desc()).limit(limit))).scalars().all()
        llm = (await session.execute(select(LlmCall).where(
            LlmCall.org_id == org_id, cast(LlmCall.response, String).ilike(pat))
            .order_by(LlmCall.created_at.desc()).limit(limit))).scalars().all()
    return {"messages": [message_to_dict(m) for m in msgs],
            "turns": [turn_to_dict(t) for t in turns],
            "toolCalls": [tool_to_dict(t) | {"result": t.result[:500]} for t in tools],
            "llmCalls": [llm_to_dict(c) for c in llm]}


@router.get("/orgs/{org_id}/stats")
async def stats(org_id: str, days: int = 14) -> dict[str, Any]:
    since = utcnow() - timedelta(days=days)
    async with SessionLocal() as session:
        per_agent = (await session.execute(select(
            LlmCall.agent_id, func.count(), func.sum(LlmCall.input_tokens),
            func.sum(LlmCall.output_tokens), func.sum(LlmCall.cost_usd),
            func.avg(LlmCall.latency_ms), func.count(LlmCall.error))
            .where(LlmCall.org_id == org_id, LlmCall.created_at >= since)
            .group_by(LlmCall.agent_id))).all()
        per_day = (await session.execute(select(
            cast(LlmCall.created_at, Date), func.sum(LlmCall.input_tokens),
            func.sum(LlmCall.output_tokens), func.sum(LlmCall.cost_usd), func.count())
            .where(LlmCall.org_id == org_id, LlmCall.created_at >= since)
            .group_by(cast(LlmCall.created_at, Date)).order_by(cast(LlmCall.created_at, Date))
        )).all()
        per_model = (await session.execute(select(
            LlmCall.model, func.count(), func.sum(LlmCall.input_tokens + LlmCall.output_tokens),
            func.sum(LlmCall.cost_usd)).where(LlmCall.org_id == org_id,
                                              LlmCall.created_at >= since)
            .group_by(LlmCall.model))).all()
        per_tool = (await session.execute(select(
            ToolCall.name, func.count(), func.avg(ToolCall.duration_ms),
            func.count().filter(ToolCall.status != "ok"))
            .where(ToolCall.org_id == org_id, ToolCall.created_at >= since)
            .group_by(ToolCall.name))).all()
        turns = (await session.execute(select(Turn.status, func.count()).where(
            Turn.org_id == org_id, Turn.started_at >= since).group_by(Turn.status))).all()
        heat = (await session.execute(select(
            func.extract("dow", LlmCall.created_at), func.extract("hour", LlmCall.created_at),
            func.count()).where(LlmCall.org_id == org_id, LlmCall.created_at >= since)
            .group_by(func.extract("dow", LlmCall.created_at),
                      func.extract("hour", LlmCall.created_at)))).all()
        names = dict((await session.execute(select(Agent.id, Agent.name).where(
            Agent.org_id == org_id))).all())
    return {
        "agents": [{"agentId": a, "name": names.get(a, a), "calls": n,
                    "inputTokens": int(i or 0), "outputTokens": int(o or 0),
                    "costUsd": float(c or 0), "avgLatencyMs": float(lat or 0),
                    "errors": int(err or 0)} for a, n, i, o, c, lat, err in per_agent],
        "days": [{"day": d.isoformat(), "inputTokens": int(i or 0), "outputTokens": int(o or 0),
                  "costUsd": float(c or 0), "calls": n} for d, i, o, c, n in per_day],
        "models": [{"model": m, "calls": n, "tokens": int(t or 0), "costUsd": float(c or 0)}
                   for m, n, t, c in per_model],
        "tools": [{"name": n, "calls": c, "avgMs": float(ms or 0), "failures": int(f or 0)}
                  for n, c, ms, f in per_tool],
        "turns": {s: n for s, n in turns},
        "heatmap": [{"dow": int(d), "hour": int(h), "calls": n} for d, h, n in heat],
    }


@router.get("/orgs/{org_id}/events")
async def events(org_id: str, after: int = 0, limit: int = 1000,
                 last: int | None = None) -> list[dict[str, Any]]:
    """Events after ``after`` (catch-up), or the ``last`` N events (history)."""
    if last is not None:
        return await bus.recent(org_id, min(max(last, 1), 2000))
    return await bus.since(org_id, after, min(limit, 5000))
