"""Event bus: every observable runtime fact flows through here.

- Persistent events get a global monotonic ``seq`` from the ``events`` table
  (allocation + commit are serialized so ``seq`` order == commit order, which
  is what makes reconnect catch-up gap-free).
- Ephemeral events (token streams, typing indicators) skip the database.
- Fan-out never blocks publishers: each subscriber owns a bounded queue; a
  subscriber that falls behind is flagged ``overflowed`` and must resync.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import EventLog

log = logging.getLogger(__name__)


@dataclass(eq=False)
class Subscription:
    org_id: str | None  # None = all orgs
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(4000))
    overflowed: bool = False

    def offer(self, event: dict[str, Any]) -> None:
        if self.org_id is not None and event.get("orgId") not in (self.org_id, None):
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.overflowed = True


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = asyncio.Lock()

    def subscribe(self, org_id: str | None = None) -> Subscription:
        sub = Subscription(org_id)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        self._subs.discard(sub)

    async def publish(
        self,
        type: str,
        payload: dict[str, Any] | None = None,
        *,
        org_id: str | None = None,
        agent_id: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        payload = payload or {}
        event: dict[str, Any] = {
            "seq": None,
            "type": type,
            "orgId": org_id,
            "agentId": agent_id,
            "ts": int(time.time() * 1000),
            "payload": payload,
        }
        if persist:
            try:
                async with self._lock, SessionLocal() as session:
                    row = EventLog(org_id=org_id, type=type, agent_id=agent_id, payload=payload)
                    session.add(row)
                    await session.commit()
                    event["seq"] = row.seq
                    self._fanout(event)
                return event
            except Exception:  # noqa: BLE001 - observability must never break the runtime
                log.exception("failed to persist event %s", type)
        self._fanout(event)
        return event

    def _fanout(self, event: dict[str, Any]) -> None:
        for sub in list(self._subs):
            sub.offer(event)

    async def since(
        self, org_id: str | None, after_seq: int, limit: int = 2000
    ) -> list[dict[str, Any]]:
        async with SessionLocal() as session:
            stmt = select(EventLog).where(EventLog.seq > after_seq).order_by(EventLog.seq)
            if org_id is not None:
                stmt = stmt.where(EventLog.org_id == org_id)
            rows = (await session.execute(stmt.limit(limit))).scalars().all()
        return [
            {
                "seq": r.seq,
                "type": r.type,
                "orgId": r.org_id,
                "agentId": r.agent_id,
                "ts": int(r.ts.timestamp() * 1000) if r.ts else 0,
                "payload": r.payload,
            }
            for r in rows
        ]

    async def recent(self, org_id: str, n: int = 200) -> list[dict[str, Any]]:
        """The org's last ``n`` persisted events, oldest first."""
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(EventLog).where(EventLog.org_id == org_id)
                .order_by(EventLog.seq.desc()).limit(n))).scalars().all()
        return [
            {"seq": r.seq, "type": r.type, "orgId": r.org_id, "agentId": r.agent_id,
             "ts": int(r.ts.timestamp() * 1000) if r.ts else 0, "payload": r.payload}
            for r in reversed(rows)
        ]

    async def head(self) -> int:
        async with SessionLocal() as session:
            from sqlalchemy import func

            return int(await session.scalar(select(func.coalesce(func.max(EventLog.seq), 0))))


bus = EventBus()
