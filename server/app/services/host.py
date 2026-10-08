"""The link to Pantheon Host, the helper on the user's computer (``host/main.py``).

Pantheon Host long-polls this backend for operations and posts the results back:
Kronos's work (files in allowed folders, changes to Pantheon's code) and recording
the computer for Shadow. It says what it can do in its hello ("capabilities"), and
it decides what it may touch (the Pantheon repo plus folders the user listed in its
own settings), so nothing in the container can widen its reach.

It authenticates with a token the backend writes to ``data/host.token``, a folder
only the backend container and the computer can read.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import time
from typing import Any

from app.core.config import settings
from app.core.ids import new_id
from app.tools.base import ToolError

POLL_GRACE = 45.0  # seconds without a poll before the runner counts as gone

NOT_CONNECTED = (
    "Pantheon Host isn't running on the user's computer, so I can't reach it right now. "
    "Ask the user to start it from the Pantheon folder: python host/main.py"
)


def token() -> str:
    path = settings.data_path / "host.token"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def check_token(given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(given.encode(), token().encode())


class Bridge:
    def __init__(self) -> None:
        self.jobs: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.pending: dict[str, asyncio.Future[Any]] = {}
        self.info: dict[str, Any] = {}
        self.last_seen = 0.0

    @property
    def connected(self) -> bool:
        return time.monotonic() - self.last_seen < POLL_GRACE

    def can(self, capability: str) -> bool:
        return self.connected and capability in (self.info.get("capabilities") or [])

    def recording(self) -> str | None:
        """The Shadow recording this computer is recording right now, if any."""
        rec = self.info.get("recording") if self.connected else None
        return rec.get("sessionId") if isinstance(rec, dict) else None

    def status(self) -> dict[str, Any]:
        return {"connected": self.connected, "info": self.info or None,
                "secondsSinceSeen": round(time.monotonic() - self.last_seen) if self.last_seen else None}

    def hello(self, info: dict[str, Any]) -> None:
        self.info = info
        self.last_seen = time.monotonic()

    async def call(self, op: str, args: dict[str, Any], timeout: float = 120) -> Any:
        if not self.connected:
            raise ToolError(NOT_CONNECTED)
        job_id = new_id("kj")
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self.pending[job_id] = fut
        await self.jobs.put({"id": job_id, "op": op, "args": args, "timeout": timeout})
        try:
            return await asyncio.wait_for(fut, timeout + 30)
        except TimeoutError:
            raise ToolError(f"Pantheon Host didn't answer within {int(timeout)} s") from None
        finally:
            self.pending.pop(job_id, None)

    async def next_job(self, wait: float) -> dict[str, Any] | None:
        self.last_seen = time.monotonic()
        while True:
            try:
                job = await asyncio.wait_for(self.jobs.get(), wait)
            except TimeoutError:
                return None
            finally:
                self.last_seen = time.monotonic()
            if job["id"] in self.pending:  # skip jobs whose caller already gave up
                return job

    def resolve(self, job_id: str, ok: bool, result: Any, error: str | None) -> None:
        self.last_seen = time.monotonic()
        fut = self.pending.get(job_id)
        if fut is None or fut.done():
            return
        if ok:
            fut.set_result(result)
        else:
            fut.set_exception(ToolError(error or "Pantheon Host reported an error"))


bridge = Bridge()
