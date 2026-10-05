"""Messaging: channels, DMs, inbox deliveries.

Delivery rules (the only way work reaches an agent):
- A message posted to a channel is delivered to members other than the sender
  (``notify='mentions'`` channels deliver only to @mentioned members).
- A message whose causal ``depth`` exceeds the org's ``max_chain_depth`` is
  stored but not delivered (recorded as ``dropped``), so agents can never
  ping-pong forever.
- The human ("user") never gets deliveries; messages to them land in the user
  inbox for the UI instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import signals
from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Agent, Channel, Delivery, Message, Org, Relationship, UserInboxItem

USER = "user"
SYSTEM = "system"
DEFAULT_MAX_DEPTH = 40


class CommsError(ValueError):
    """A messaging request that cannot be honoured (bad target, policy...)."""


@dataclass
class Sender:
    type: str  # user | agent | system
    id: str
    turn_id: str | None = None
    depth: int = 0  # depth of the message that caused this send

    @classmethod
    def user(cls) -> Sender:
        return cls("user", USER)

    @classmethod
    def system(cls, depth: int = 0) -> Sender:
        return cls("system", SYSTEM, depth=depth)


async def _turn_has_thinking(session: AsyncSession, turn_id: str) -> bool:
    """Did the model return any reasoning during this turn (so far)?"""
    from app.models import LlmCall

    # Checked in Python: Postgres refuses to look inside a JSON value that holds an escaped
    # NUL (e.g. tool-call args with binary content), which used to drop the agent's reply.
    responses = (await session.scalars(select(LlmCall.response).where(
        LlmCall.turn_id == turn_id, LlmCall.purpose == "turn"))).all()
    return any(isinstance(r, dict) and r.get("reasoning") for r in responses)


def dm_key(a: str, b: str) -> str:
    x, y = sorted((a, b))
    return f"dm:{x}:{y}"


def message_to_dict(m: Message) -> dict[str, Any]:
    return {
        "id": m.id,
        "orgId": m.org_id,
        "channelId": m.channel_id,
        "taskId": m.task_id,
        "replyTo": m.reply_to,
        "senderType": m.sender_type,
        "senderId": m.sender_id,
        "kind": m.kind,
        "content": m.content,
        "depth": m.depth,
        "turnId": m.turn_id,
        "meta": m.meta or {},
        "createdAt": m.created_at.isoformat() if m.created_at else utcnow().isoformat(),
    }


def channel_to_dict(c: Channel) -> dict[str, Any]:
    return {
        "id": c.id, "orgId": c.org_id, "kind": c.kind, "key": c.key, "name": c.name,
        "topic": c.topic, "members": c.members, "notify": c.notify,
        "createdBy": c.created_by, "archived": c.archived,
    }


async def resolve_agent(session: AsyncSession, org_id: str, ref: str) -> Agent | None:
    """Find an agent in an org by id, exact name, or unambiguous name prefix."""
    ref = (ref or "").strip().lstrip("@")
    if not ref:
        return None
    agent = await session.get(Agent, ref)
    if agent is not None and agent.org_id == org_id:
        return agent
    agents = (await session.execute(select(Agent).where(Agent.org_id == org_id))).scalars().all()
    low = ref.lower()
    exact = [a for a in agents if a.name.lower() == low]
    if len(exact) == 1:
        return exact[0]
    prefix = [a for a in agents if a.name.lower().startswith(low)]
    return prefix[0] if len(prefix) == 1 else None


async def ensure_dm(session: AsyncSession, org_id: str, a: str, b: str) -> Channel:
    key = dm_key(a, b)
    ch = (
        await session.execute(select(Channel).where(Channel.org_id == org_id, Channel.key == key))
    ).scalar_one_or_none()
    if ch is None:
        ch = Channel(org_id=org_id, kind="dm", key=key, name=key, members=sorted({a, b}),
                     created_by=a)
        session.add(ch)
        await session.flush()
    return ch


async def find_channel(session: AsyncSession, org_id: str, ref: str) -> Channel | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    ch = await session.get(Channel, ref)
    if ch is not None and ch.org_id == org_id:
        return ch
    key = ref if ref.startswith(("#", "dm:", "meeting:")) else f"#{ref.lower()}"
    return (
        await session.execute(select(Channel).where(Channel.org_id == org_id, Channel.key == key))
    ).scalar_one_or_none()


async def create_channel(
    org_id: str, name: str, members: list[str], topic: str = "", created_by: str = USER,
    notify: str = "all",
) -> Channel:
    slug = re.sub(r"[^a-z0-9_-]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise CommsError("channel name is empty")
    async with SessionLocal() as session:
        key = f"#{slug}"
        existing = (
            await session.execute(
                select(Channel).where(Channel.org_id == org_id, Channel.key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise CommsError(f"channel {key} already exists")
        ids: list[str] = []
        for ref in members:
            if ref == USER:
                ids.append(USER)
                continue
            agent = await resolve_agent(session, org_id, ref)
            if agent is None:
                raise CommsError(f"unknown member '{ref}'")
            ids.append(agent.id)
        if created_by != USER and created_by not in ids:
            ids.append(created_by)
        ch = Channel(org_id=org_id, kind="channel", key=key, name=slug, topic=topic,
                     members=sorted(set(ids)), created_by=created_by, notify=notify)
        session.add(ch)
        await session.commit()
    await bus.publish("channel.created", channel_to_dict(ch), org_id=org_id)
    return ch


async def may_contact(session: AsyncSession, org: Org, sender_id: str, target_id: str) -> bool:
    """Communication policy. ``open`` (default): anyone may DM anyone.
    ``structured``: only related agents, or agents sharing a channel."""
    if sender_id in (USER, SYSTEM) or target_id == USER:
        return True
    if (org.settings or {}).get("comm_policy", "open") == "open":
        return True
    rel = (
        await session.execute(
            select(Relationship.id).where(
                Relationship.org_id == org.id,
                or_(
                    (Relationship.from_id == sender_id) & (Relationship.to_id == target_id),
                    (Relationship.from_id == target_id) & (Relationship.to_id == sender_id),
                ),
            ).limit(1)
        )
    ).first()
    if rel:
        return True
    channels = (
        await session.execute(
            select(Channel).where(Channel.org_id == org.id, Channel.kind == "channel")
        )
    ).scalars()
    return any(sender_id in c.members and target_id in c.members for c in channels)


def _mentions(content: str, agents: list[Agent]) -> set[str]:
    low = content.lower()
    out = set()
    for a in agents:
        if f"@{a.name.lower()}" in low or f"@{a.id}" in low:
            out.add(a.id)
    if "@user" in low:
        out.add(USER)
    return out


async def post(
    org_id: str,
    sender: Sender,
    content: str,
    *,
    channel: Channel | None = None,
    to_agents: list[str] | None = None,
    kind: str = "chat",
    task_id: str | None = None,
    reply_to: str | None = None,
    meta: dict[str, Any] | None = None,
    session: AsyncSession | None = None,
    deliver: bool = True,
) -> Message:
    """Store a message and deliver it.

    ``channel`` messages go to channel members; ``to_agents`` (no channel) are
    direct inbox notifications (task updates, system notices). ``deliver=False``
    records the message (e.g. a live meeting transcript) without inbox items.
    """
    content = (content or "").strip()
    if not content:
        raise CommsError("message is empty")
    own = session is None
    s = session or SessionLocal()
    try:
        org = await s.get(Org, org_id)
        if org is None:
            raise CommsError(f"org '{org_id}' not found")
        max_depth = int((org.settings or {}).get("max_chain_depth", DEFAULT_MAX_DEPTH))
        depth = sender.depth + 1 if sender.type == "agent" else sender.depth
        if sender.type == "agent" and sender.turn_id and await _turn_has_thinking(
                s, sender.turn_id):
            meta = {**(meta or {}), "thinking": True}
        msg = Message(
            org_id=org_id, channel_id=channel.id if channel else None, task_id=task_id,
            reply_to=reply_to, sender_type=sender.type, sender_id=sender.id, kind=kind,
            content=content, depth=depth, turn_id=sender.turn_id, meta=meta or {},
        )
        s.add(msg)
        await s.flush()

        agents = (await s.execute(select(Agent).where(Agent.org_id == org_id))).scalars().all()
        by_id = {a.id: a for a in agents}
        if channel is not None:
            members = [m for m in channel.members if m != sender.id]
            if channel.notify == "mentions" and channel.kind == "channel":
                mentioned = _mentions(content, agents)
                members = [m for m in members if m in mentioned]
        else:
            members = [m for m in (to_agents or []) if m != sender.id]
        if not deliver:
            members = []

        woken: list[str] = []
        for rid in dict.fromkeys(members):
            if rid == USER:
                s.add(UserInboxItem(org_id=org_id, kind="message", ref_id=msg.id,
                                    title=content[:200]))
                continue
            agent = by_id.get(rid)
            if agent is None or agent.status == "disabled":
                continue
            status, note = "pending", ""
            if depth > max_depth:
                status, note = "dropped", f"causal depth {depth} exceeds limit {max_depth}"
            s.add(Delivery(org_id=org_id, agent_id=rid, message_id=msg.id, status=status,
                           note=note))
            if status == "pending":
                woken.append(rid)
            else:
                await bus.publish(
                    "system.warning",
                    {"reason": f"message to {agent.name} dropped: {note}", "messageId": msg.id},
                    org_id=org_id, agent_id=rid,
                )
        if own:
            await s.commit()
        else:
            await s.flush()
    finally:
        if own:
            await s.close()
    await bus.publish("message.created", message_to_dict(msg), org_id=org_id,
                      agent_id=sender.id if sender.type == "agent" else None)
    for rid in woken:
        signals.wake(rid)
    return msg


async def send_direct(
    org_id: str, sender: Sender, target_ref: str, content: str, *, kind: str = "chat",
    task_id: str | None = None, reply_to: str | None = None, meta: dict[str, Any] | None = None,
) -> Message:
    """DM between two participants (agent↔agent, agent↔user)."""
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise CommsError(f"org '{org_id}' not found")
        if target_ref.strip().lower() in (USER, "the user", "human", "@user"):
            target_id = USER
        else:
            agent = await resolve_agent(session, org_id, target_ref)
            if agent is None:
                raise CommsError(f"no colleague named '{target_ref}'")
            target_id = agent.id
        if target_id == sender.id:
            raise CommsError("you cannot message yourself")
        if not await may_contact(session, org, sender.id, target_id):
            raise CommsError(
                "the org's communication policy does not let you contact that agent directly; "
                "use a shared channel or your manager"
            )
        channel = await ensure_dm(session, org_id, sender.id, target_id)
        await session.commit()
    return await post(org_id, sender, content, channel=channel, kind=kind, task_id=task_id,
                      reply_to=reply_to, meta=meta)


async def notify(
    org_id: str, agent_ids: list[str], content: str, *, depth: int = 0,
    task_id: str | None = None, kind: str = "notification", meta: dict[str, Any] | None = None,
) -> Message | None:
    targets = [a for a in agent_ids if a and a not in (USER, SYSTEM)]
    if not targets:
        return None
    return await post(org_id, Sender.system(depth), content, to_agents=targets, kind=kind,
                      task_id=task_id, meta=meta)


async def channel_history(
    org_id: str, channel_id: str, limit: int = 50, before: str | None = None
) -> list[Message]:
    async with SessionLocal() as session:
        stmt = select(Message).where(Message.org_id == org_id, Message.channel_id == channel_id)
        if before:
            ref = await session.get(Message, before)
            if ref is not None:
                stmt = stmt.where(Message.created_at < ref.created_at)
        rows = (
            await session.execute(stmt.order_by(Message.created_at.desc()).limit(limit))
        ).scalars().all()
    return list(reversed(rows))
