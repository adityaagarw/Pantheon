"""Argus's watches: organizations the user asked it to keep an eye on.

Each watch wakes Argus on a schedule with a "scheduled check" notice; Argus
then investigates with its oversight tools and reports or raises alerts. The
schedule lives in the ``settings`` table so it survives restarts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Setting

log = logging.getLogger(__name__)
KEY = "argus.watches"
MIN_EVERY = 5


async def watches() -> dict[str, dict[str, Any]]:
    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
    return dict(row.value) if row and isinstance(row.value, dict) else {}


async def _save(value: dict[str, dict[str, Any]]) -> None:
    async with SessionLocal() as session:
        row = await session.get(Setting, KEY)
        if row is None:
            session.add(Setting(key=KEY, value=value))
        else:
            row.value = value
        await session.commit()
    await bus.publish("argus.watches", {"watches": value})


async def set_watch(org_id: str, org_name: str, every_minutes: int, focus: str) -> dict[str, Any]:
    all_ = await watches()
    w = {"orgId": org_id, "orgName": org_name, "everyMinutes": max(MIN_EVERY, every_minutes),
         "focus": focus.strip(), "lastCheck": utcnow().isoformat(),
         "createdAt": (all_.get(org_id) or {}).get("createdAt") or utcnow().isoformat()}
    all_[org_id] = w
    await _save(all_)
    return w


async def remove_watch(org_id: str) -> bool:
    all_ = await watches()
    if all_.pop(org_id, None) is None:
        return False
    await _save(all_)
    return True


async def due() -> list[dict[str, Any]]:
    """Watches whose next check is due; marks them checked."""
    all_ = await watches()
    now = utcnow()
    out = []
    for w in all_.values():
        try:
            last = w.get("lastCheck")
            last_dt = datetime.fromisoformat(last) if last else None
        except ValueError:
            last_dt = None
        if last_dt is None or now - last_dt >= timedelta(minutes=int(w.get("everyMinutes", 30))):
            w["lastCheck"] = now.isoformat()
            out.append(w)
    if out:
        await _save(all_)
    return out


async def tick() -> int:
    from app.services import comms, supervisor
    from app.services.supervisor import SYSTEM_ORG_ID

    fired = 0
    for w in await due():
        argus = await supervisor.meta_agent_id("argus")
        focus = f"\nWhat to watch for: {w['focus']}" if w.get("focus") else ""
        await comms.notify(
            SYSTEM_ORG_ID, [argus],
            f"Scheduled check of {w['orgName']} [org {w['orgId']}] (every "
            f"{w['everyMinutes']} min).{focus}\nInvestigate, raise_alert for anything the user "
            "must act on, and end with a two-line status.", kind="watch")
        fired += 1
    return fired


async def run_forever() -> None:
    while True:
        try:
            await asyncio.sleep(settings.argus_tick_seconds)
            await tick()
            from app.services import activities, schedules

            await activities.tick()
            await schedules.tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the watcher must never die
            log.exception("argus watch tick failed")
