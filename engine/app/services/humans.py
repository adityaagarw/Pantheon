"""Humans in an organization: agents of kind ``human`` (app/agents/kinds.py).

They never take turns. What's sent to them waits (a pending delivery, and an item in the
user's Inbox) until someone answers as them in the interface. An answer is a turn like
any agent's: it goes back to whoever wrote, and finishes a flow step it was the answer to.
That's human in the loop: a person can be a step in a flow, a reviewer on a task, or a
lead, alongside the agents.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.ids import new_id
from app.events import bus
from app.models import Agent, Delivery, Message, Org, Turn, UserInboxItem


class HumanError(ValueError):
    pass


async def _human(agent_id: str) -> tuple[Agent, Org]:
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        org = await session.get(Org, agent.org_id) if agent else None
    if agent is None or org is None:
        raise HumanError("agent not found")
    if agent.kind != "human":
        raise HumanError(f"{agent.name} isn't a person")
    return agent, org


async def waiting(agent_id: str) -> list[Message]:
    """What's waiting for this person, oldest first."""
    await _human(agent_id)
    async with SessionLocal() as session:
        return list((await session.execute(
            select(Message).join(Delivery, Delivery.message_id == Message.id).where(
                Delivery.agent_id == agent_id, Delivery.status == "pending")
            .order_by(Message.created_at))).scalars())


async def answer(agent_id: str, content: str, message_ids: list[str] | None = None) -> dict[str, Any]:
    """Answer as this person: the waiting messages (or the ones given) are theirs to reply
    to; the reply goes to whoever sent them."""
    from app.agents import external
    from app.agents.runtime import runtime

    agent, org = await _human(agent_id)
    content = content.strip()
    if not content:
        raise HumanError("write the answer")
    msgs = await waiting(agent_id)
    if message_ids:
        msgs = [m for m in msgs if m.id in set(message_ids)]
    if not msgs:
        raise HumanError(f"nothing is waiting for {agent.name}")
    turn_id = new_id("trn")
    _content, depth, direct = await runtime._format_inbox(agent, msgs)
    runtime._inbox_images = []
    async with SessionLocal() as session:
        session.add(Turn(id=turn_id, org_id=org.id, agent_id=agent.id,
                         trigger_message_ids=[m.id for m in msgs]))
        await session.execute(update(Delivery).where(
            Delivery.agent_id == agent.id, Delivery.message_id.in_([m.id for m in msgs]))
            .values(status="done", turn_id=turn_id, lease_until=None))
        await session.execute(update(UserInboxItem).where(
            UserInboxItem.kind == "human", UserInboxItem.ref_id == agent.id)
            .values(read=True))
        await session.commit()
    await bus.publish("turn.started", {"turnId": turn_id, "messageIds": [m.id for m in msgs]},
                      org_id=org.id, agent_id=agent.id)
    await external.complete(runtime, agent, org, turn_id, content, direct, depth)
    return {"turnId": turn_id, "answered": [m.id for m in msgs]}
