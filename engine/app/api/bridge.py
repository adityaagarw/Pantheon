"""The engine side of the tool bridge for external agents (app/agents/external.py,
app/mcpbridge.py). Each call carries the token of the turn it belongs to."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from app.agents import external
from app.agents.graph import _record_tool_call
from app.core.db import SessionLocal
from app.core.ids import new_id
from app.events import bus
from app.models import Agent, Org
from app.tools.base import ToolContext
from app.tools.resolve import execute

router = APIRouter(prefix="/api/v1/bridge", tags=["bridge"])


async def _grant(authorization: str | None) -> tuple[external.Grant, Org, Agent]:
    token = (authorization or "").removeprefix("Bearer ").strip()
    g = external.grant(token) if token else None
    if g is None:
        raise HTTPException(401, "this bridge token isn't valid (its turn is over)")
    async with SessionLocal() as session:
        agent = await session.get(Agent, g.agent_id)
        org = await session.get(Org, agent.org_id) if agent else None
    if agent is None or org is None:
        raise HTTPException(404, "agent not found")
    return g, org, agent


@router.get("/tools")
async def tools(authorization: str | None = Header(default=None)) -> list[dict[str, Any]]:
    g, org, agent = await _grant(authorization)
    available = await external.bridge_tools(org, agent)
    return [{"name": t.name, "description": t.description, "parameters": t.parameters}
            for name, t in available.items() if name in g.tools]


@router.post("/call")
async def call(body: dict[str, Any] = Body(...),
               authorization: str | None = Header(default=None)) -> dict[str, Any]:
    g, org, agent = await _grant(authorization)
    name = str(body.get("name") or "")
    args = body.get("arguments") or {}
    available = await external.bridge_tools(org, agent)
    t = available.get(name)
    if t is None or name not in g.tools:
        return {"ok": False, "text": f"ERROR: tool '{name}' isn't available to you"}
    if t.approval == "ask":
        # Like a Pantheon agent's call: it waits in the user's Inbox for their decision.
        if g.live is None:
            return {"ok": False, "text": f"DENIED: {name} needs the user's approval"}
        approved, note = await g.live.ask(name, args)
        if not approved:
            return {"ok": False, "text": f"DENIED: the user did not approve this call "
                                         f"({note or 'no reason given'})."}
    tc_id = new_id("tc")
    started = time.monotonic()
    await bus.publish("tool.started", {"toolCallId": tc_id, "tool": name, "args": args,
                                       "source": "bridge", "turnId": g.turn_id},
                      org_id=org.id, agent_id=agent.id)
    ctx = ToolContext(org=org, agent=agent, turn_id=g.turn_id, tool_call_id=tc_id, depth=g.depth)
    out, ok = await execute(t, args, ctx)
    duration = int((time.monotonic() - started) * 1000)
    status = "ok" if ok else "error"
    await _record_tool_call(tc_id, org.id, agent.id, g.turn_id, name, args, status, out, duration)
    await bus.publish("tool.completed", {"toolCallId": tc_id, "tool": name, "status": status,
                                         "durationMs": duration, "preview": str(out)[:600],
                                         "turnId": g.turn_id},
                      org_id=org.id, agent_id=agent.id)
    return {"ok": ok, "text": str(out)}
