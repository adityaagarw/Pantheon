"""Town Square behaviours: handcuffs restrain, knives can be drawn or put away,
and police radios reach every officer wherever they are.

This file doubles as the reference for writing plugins:

* ``api.tool(...)`` registers an agent tool (grant it to agents on the Team tab,
  or in a template's ``tools`` list). Handlers are ``async (args, ctx) -> str``;
  ``ctx.org`` / ``ctx.agent`` are the caller's org and agent.
* ``api.on_use(pattern)`` runs when an agent calls ``use_object`` on an object
  whose name or asset key matches the glob. Mutate ``event.obj`` (state, holder)
  and return a sentence describing what happened; Pantheon saves the object,
  shows it in the 3D office and tells whoever is involved.
"""

from __future__ import annotations

import re

VERB = {
    "restrain": re.compile(r"\b(cuff|handcuff|restrain|arrest|detain|use)\b", re.I),
    "release": re.compile(r"\b(uncuff|release|unlock|free|remove)\b", re.I),
    "draw": re.compile(r"\b(draw|brandish|point|wave|raise|hold up|show|threaten)\b", re.I),
    "stow": re.compile(r"\b(sheathe|stow|wrap|put away|hide|lower|drop|put down)\b", re.I),
}


def register(api) -> None:
    @api.on_use("handcuffs")
    async def handcuffs(event) -> str | None:
        obj, target = event.obj, event.target
        if VERB["release"].search(event.action):
            who = obj.state.get("restraining")
            obj.holder_id = event.actor.id
            obj.state = {}
            return f"{who} is released from the handcuffs." if who else None
        if target is None:
            return "Handcuffs need someone to be used on (target)."
        if obj.state.get("restraining"):
            return f"The handcuffs are already on {obj.state['restraining']}."
        if VERB["restrain"].search(event.action):
            obj.holder_id = target.id  # they're wearing them now
            obj.state = {"restraining": target.name}
            return f"{target.name} is now in handcuffs and can't use their hands."
        return None

    @api.on_use("*knife*")
    async def knife(event) -> str | None:
        if VERB["stow"].search(event.action):
            event.obj.state = {**event.obj.state, "drawn": False}
            return "The blade is put away."
        if VERB["draw"].search(event.action):
            event.obj.state = {**event.obj.state, "drawn": True}
            return "The blade is out in the open — everyone nearby can see it."
        return None

    @api.tool(
        "radio_call",
        "Speak on the police radio: every officer hears you wherever they are. "
        "Needs a police radio in your hands.",
        {"type": "object", "properties": {"message": {"type": "string"}},
         "required": ["message"]},
    )
    async def radio_call(args: dict, ctx) -> str:
        from sqlalchemy import select

        from app.core.db import SessionLocal
        from app.models import Agent, WorldObject
        from app.services import comms

        async with SessionLocal() as session:
            radio = (await session.execute(select(WorldObject).where(
                WorldObject.org_id == ctx.org.id, WorldObject.holder_id == ctx.agent.id,
                WorldObject.asset.like("%police-radio%")))).scalars().first()
            officers = (await session.execute(select(Agent).where(
                Agent.org_id == ctx.org.id, Agent.id != ctx.agent.id,
                Agent.role.ilike("%officer%")))).scalars().all()
        if radio is None:
            return "ERROR: you don't have a police radio in your hands."
        if not officers:
            return "Static. Nobody else is on this channel."
        await comms.post(ctx.org.id, ctx.sender, f"📻 {args['message']}",
                         to_agents=[o.id for o in officers], kind="speech",
                         meta={"room": "police radio"})
        return "Heard on the radio by: " + ", ".join(o.name for o in officers) + "."
