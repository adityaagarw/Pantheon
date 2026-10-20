"""Tools for leads: steering the people they lead (app/agents/steering.py).

Anyone who manages someone has these, whatever tools they were given.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Agent, Relationship
from app.services import comms
from app.tools.base import B, S, ToolContext, ToolError, obj, tool

LEAD = ("steer", "plan_flow")  # plan_flow: tools/flowtools.py


async def reports(ctx: ToolContext) -> list[Agent]:
    """The people this agent manages."""
    async with SessionLocal() as session:
        ids = set((await session.execute(select(Relationship.to_id).where(
            Relationship.org_id == ctx.org.id, Relationship.from_id == ctx.agent.id,
            Relationship.kind == "manages"))).scalars())
        return list((await session.execute(select(Agent).where(Agent.id.in_(ids)))).scalars()) \
            if ids else []


async def report(ctx: ToolContext, name: str) -> Agent:
    for a in await reports(ctx):
        if a.name.lower() == name.strip().lower() or a.id == name.strip():
            return a
    raise ToolError(f"'{name}' isn't one of the people you lead")


@tool("steer", "Correct course for someone you lead while they work: your message reaches "
      "them before their next step, in the middle of what they're doing, instead of after "
      "they finish. Use it when they're going the wrong way, missed something, or should "
      "change priority. `stop: true` also stops what they're doing now; your message is "
      "then what they do next.", obj({"person": S, "message": S, "stop": B}, ["person", "message"]),
      category="lead")
async def steer(args: dict, ctx: ToolContext) -> str:
    from app.agents.runtime import runtime

    a = await report(ctx, args["person"])
    message = str(args["message"]).strip()
    if not message:
        raise ToolError("say what they should do differently")
    working = a.runtime_status == "working"
    stopped = await runtime.stop_turn(a.id) if args.get("stop") else False
    await comms.send_direct(ctx.org.id, ctx.sender, a.id, message, meta={"steer": True})
    if stopped:
        return f"Stopped what {a.name} was doing; your message is what they do next."
    if working:
        return f"{a.name} is working: they'll read this before their next step."
    return f"{a.name} isn't working right now: this starts their next turn."
