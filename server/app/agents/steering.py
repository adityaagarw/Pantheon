"""Steering: the user's messages that reach an agent in the middle of a turn.

A message the user sends with ``steer`` set (the composer does this while the agent is
working) is not left for the next turn: before its next model call, the working agent
takes it and reads it as part of the turn it's in, so the user can correct course,
add a detail or say stop without waiting.
"""

from __future__ import annotations

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Message

STEER_NOTE = ("[New message while you were working. Take it into account now: it may "
              "change what you should do next.]")


async def take(agent_id: str, turn_id: str | None) -> list[Message]:
    """Claim the user's pending steering messages for this agent (oldest first)."""
    async with SessionLocal() as session:
        ids = (await session.execute(text("""
            UPDATE deliveries d SET status = 'done', turn_id = :turn, lease_until = NULL
            FROM messages m
            WHERE d.message_id = m.id AND d.agent_id = :agent AND d.status = 'pending'
              AND m.sender_type = 'user' AND (m.meta ->> 'steer') = 'true'
            RETURNING d.message_id
        """), {"agent": agent_id, "turn": turn_id})).scalars().all()
        await session.commit()
        if not ids:
            return []
        return list((await session.execute(select(Message).where(Message.id.in_(ids))
                                           .order_by(Message.created_at))).scalars())
