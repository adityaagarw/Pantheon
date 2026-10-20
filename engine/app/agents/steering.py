"""Steering: messages that reach an agent in the middle of a turn.

A message sent with ``steer`` set is not left for the next turn: before its next model
call, the working agent takes it and reads it as part of the turn it's in. The user's
composer does this while the agent is working, and a lead's steer tool
(tools/leadtools.py) does it for the people they lead, so either can correct course, add
a detail or say stop without waiting.
"""

from __future__ import annotations

from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Message

STEER_NOTE = ("[New message while you were working. Take it into account now: it may "
              "change what you should do next.]")


async def take(agent_id: str, turn_id: str | None) -> list[Message]:
    """Claim the pending steering messages for this agent (oldest first): the user's and
    its leads' (only the steer tool marks an agent's message, and only a lead has it)."""
    async with SessionLocal() as session:
        ids = (await session.execute(text("""
            UPDATE deliveries SET status = 'done', turn_id = :turn, lease_until = NULL
            WHERE agent_id = :agent AND status = 'pending' AND message_id IN (
                SELECT id FROM messages
                WHERE sender_type IN ('user', 'agent')
                  AND json_extract(meta, '$.steer') IN (1, 'true'))
            RETURNING message_id
        """), {"agent": agent_id, "turn": turn_id})).scalars().all()
        await session.commit()
        if not ids:
            return []
        return list((await session.execute(select(Message).where(Message.id.in_(ids))
                                           .order_by(Message.created_at))).scalars())
