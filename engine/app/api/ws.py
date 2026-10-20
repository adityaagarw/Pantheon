"""WebSocket event stream: catch-up from ``after`` then live events.

Ordering contract: the client receives every persisted event with
seq > after exactly once and in order. Live events that arrive during the
catch-up are buffered and de-duplicated by seq. If the client falls too far
behind (its queue overflows) the server sends ``resync`` and closes; the
client reconnects with its last seq.
"""

from __future__ import annotations

import asyncio
import contextlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.events import bus

router = APIRouter()


@router.websocket("/api/v1/ws")
async def events_ws(ws: WebSocket) -> None:
    await ws.accept()
    org_id = ws.query_params.get("org") or None
    try:
        after = int(ws.query_params.get("after") or 0)
    except ValueError:
        after = 0
    sub = bus.subscribe(org_id)
    try:
        if after <= 0:
            after = await bus.head()
            await ws.send_json({"type": "hello", "seq": after})
        else:
            while True:
                batch = await bus.since(org_id, after, 2000)
                for ev in batch:
                    await ws.send_json(ev)
                    after = ev["seq"]
                if len(batch) < 2000:
                    break
            await ws.send_json({"type": "hello", "seq": after})

        async def pump() -> None:
            nonlocal after
            while True:
                ev = await sub.queue.get()
                if sub.overflowed:
                    await ws.send_json({"type": "resync", "seq": after})
                    await ws.close()
                    return
                seq = ev.get("seq")
                if seq is not None:
                    if seq <= after:
                        continue
                    after = seq
                await ws.send_json(ev)

        async def drain_client() -> None:
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_text("pong")

        tasks = [asyncio.create_task(pump()), asyncio.create_task(drain_client())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in done:
            with contextlib.suppress(WebSocketDisconnect, asyncio.CancelledError, RuntimeError):
                t.result()
    except WebSocketDisconnect:
        pass
    finally:
        bus.unsubscribe(sub)
