"""How many agents may work at once: a global limit and, optionally, one per organization.

Both can change while Pantheon runs (Settings, and each organization's settings). Agents
over a limit wait their turn, first come first served, except that someone whose own
organization is at its limit doesn't hold up agents from other organizations.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class Slots:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.org_limits: dict[str, int] = {}  # 0 or missing: no limit of its own
        self.running: dict[str, str] = {}  # agent id -> org id
        self.waiting: list[tuple[str, str]] = []  # (agent id, org id), oldest first
        self._cond = asyncio.Condition()

    def _fits(self, org_id: str) -> bool:
        if len(self.running) >= self.limit:
            return False
        own = self.org_limits.get(org_id) or 0
        return not own or sum(1 for o in self.running.values() if o == org_id) < own

    def _my_turn(self, agent_id: str, org_id: str) -> bool:
        if not self._fits(org_id):
            return False
        for other, other_org in self.waiting:
            if other == agent_id:
                return True
            if self._fits(other_org):
                return False  # someone who has waited longer can go first
        return True

    def would_wait(self, org_id: str) -> bool:
        return bool(self.waiting) or not self._fits(org_id)

    @asynccontextmanager
    async def hold(self, agent_id: str, org_id: str) -> AsyncIterator[None]:
        async with self._cond:
            self.waiting.append((agent_id, org_id))
            try:
                await self._cond.wait_for(lambda: self._my_turn(agent_id, org_id))
            finally:
                self.waiting.remove((agent_id, org_id))
                self._cond.notify_all()
            self.running[agent_id] = org_id
        try:
            yield
        finally:
            async with self._cond:
                self.running.pop(agent_id, None)
                self._cond.notify_all()

    async def set_limit(self, limit: int) -> None:
        async with self._cond:
            self.limit = max(1, int(limit))
            self._cond.notify_all()

    async def set_org_limit(self, org_id: str, limit: int | None) -> None:
        async with self._cond:
            if limit and int(limit) > 0:
                self.org_limits[org_id] = int(limit)
            else:
                self.org_limits.pop(org_id, None)
            self._cond.notify_all()

    def status(self) -> dict[str, int]:
        return {"limit": self.limit, "running": len(self.running), "waiting": len(self.waiting)}
