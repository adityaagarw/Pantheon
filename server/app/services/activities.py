"""Scheduled activities: recurring happenings in an organization.

A daily standup at 09:00, lunch in the kitchen at noon, the market opening, an
officer's patrol every 30 minutes. When an activity runs, its participants are
(optionally) sent to a room and told what's happening; what they do next is up
to them. Times are UTC.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Activity, Agent

log = logging.getLogger(__name__)
DAILY = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MIN_EVERY = 5


class ActivityError(ValueError):
    pass


def validate_schedule(schedule: dict[str, Any] | None) -> dict[str, Any]:
    s = dict(schedule or {})
    if s.get("every_minutes"):
        every = int(s["every_minutes"])
        if every < MIN_EVERY:
            raise ActivityError(f"every_minutes must be at least {MIN_EVERY}")
        return {"every_minutes": every}
    if s.get("daily_at"):
        if not DAILY.match(str(s["daily_at"])):
            raise ActivityError("daily_at must look like 09:30 (24h, UTC)")
        return {"daily_at": str(s["daily_at"])}
    return {}


def next_run(schedule: dict[str, Any], after: datetime) -> datetime | None:
    if every := schedule.get("every_minutes"):
        return after + timedelta(minutes=int(every))
    if at := schedule.get("daily_at"):
        h, m = (int(x) for x in str(at).split(":"))
        t = after.replace(hour=h, minute=m, second=0, microsecond=0)
        return t if t > after else t + timedelta(days=1)
    return None


def to_dict(a: Activity) -> dict[str, Any]:
    return {"id": a.id, "orgId": a.org_id, "title": a.title, "instructions": a.instructions,
            "participants": a.participants or [], "room": a.room, "schedule": a.schedule or {},
            "enabled": a.enabled, "createdBy": a.created_by,
            "nextRunAt": a.next_run_at.isoformat() if a.next_run_at else None,
            "lastRunAt": a.last_run_at.isoformat() if a.last_run_at else None}


async def _participant_ids(org_id: str, refs: list[str]) -> list[str]:
    from app.services import comms

    out = []
    async with SessionLocal() as session:
        for ref in refs or []:
            a = await comms.resolve_agent(session, org_id, str(ref))
            if a is None:
                raise ActivityError(f"no agent '{ref}' in this organization")
            out.append(a.id)
    return list(dict.fromkeys(out))


async def _check_room(org_id: str, room: str | None) -> str | None:
    from app.services import spatial

    if not room:
        return None
    w = await spatial.load_world(org_id)
    found = w.space.room(room)
    if found is None:
        raise ActivityError(f"no room '{room}' (rooms: "
                            f"{', '.join(r.name for r in w.space.rooms)})")
    return found.name


async def create(org_id: str, title: str, instructions: str = "", *,
                 participants: list[str] | None = None, room: str | None = None,
                 schedule: dict[str, Any] | None = None, created_by: str = "user") -> Activity:
    if not title.strip():
        raise ActivityError("title is required")
    sched = validate_schedule(schedule)
    a = Activity(org_id=org_id, title=title.strip()[:200], instructions=instructions.strip(),
                 participants=await _participant_ids(org_id, participants or []),
                 room=await _check_room(org_id, room), schedule=sched,
                 next_run_at=next_run(sched, utcnow()), created_by=created_by)
    async with SessionLocal() as session:
        session.add(a)
        await session.commit()
    await bus.publish("activity.saved", to_dict(a), org_id=org_id)
    return a


async def update(activity_id: str, patch: dict[str, Any]) -> Activity:
    async with SessionLocal() as session:
        a = await session.get(Activity, activity_id)
        if a is None:
            raise ActivityError("activity not found")
        if patch.get("title"):
            a.title = str(patch["title"]).strip()[:200]
        if "instructions" in patch and patch["instructions"] is not None:
            a.instructions = str(patch["instructions"]).strip()
        if "participants" in patch and patch["participants"] is not None:
            a.participants = await _participant_ids(a.org_id, patch["participants"])
        if "room" in patch:
            a.room = await _check_room(a.org_id, patch["room"])
        if "schedule" in patch:
            a.schedule = validate_schedule(patch["schedule"])
            a.next_run_at = next_run(a.schedule, utcnow())
        if "enabled" in patch and patch["enabled"] is not None:
            a.enabled = bool(patch["enabled"])
            if a.enabled and a.next_run_at is None:
                a.next_run_at = next_run(a.schedule or {}, utcnow())
        await session.commit()
    await bus.publish("activity.saved", to_dict(a), org_id=a.org_id)
    return a


async def delete(activity_id: str) -> None:
    async with SessionLocal() as session:
        a = await session.get(Activity, activity_id)
        if a is None:
            raise ActivityError("activity not found")
        await session.delete(a)
        await session.commit()
    await bus.publish("activity.deleted", {"id": activity_id}, org_id=a.org_id)


async def for_org(org_id: str) -> list[Activity]:
    async with SessionLocal() as session:
        return list((await session.execute(select(Activity).where(
            Activity.org_id == org_id).order_by(Activity.created_at))).scalars())


async def run(activity_id: str) -> list[str]:
    """Run an activity now: gather participants, tell them what's happening."""
    from app.services import comms, spatial

    async with SessionLocal() as session:
        a = await session.get(Activity, activity_id)
        if a is None:
            raise ActivityError("activity not found")
        ids = a.participants or [x for x in (await session.execute(select(Agent.id).where(
            Agent.org_id == a.org_id, Agent.status == "active",
            Agent.is_supervisor.is_(False)))).scalars()]
        agents = list((await session.execute(select(Agent).where(Agent.id.in_(ids or [""]))))
                      .scalars())
    if a.room:
        for agent in agents:
            await spatial.set_location(agent, {"kind": "room", "room": a.room},
                                       f"heads to the {a.room} for {a.title}")
    names = ", ".join(x.name for x in agents)
    text = (f"Activity: {a.title}" + (f" (in the {a.room})" if a.room else "") + "\n"
            + (a.instructions or "") + (f"\nTaking part: {names}." if names else ""))
    if agents:
        await comms.notify(a.org_id, [x.id for x in agents], text.strip(), kind="activity",
                           meta={"activityId": a.id})
    async with SessionLocal() as session:
        row = await session.get(Activity, activity_id)
        if row is not None:
            now = utcnow()
            row.last_run_at = now
            row.next_run_at = next_run(row.schedule or {}, now)
            await session.commit()
            a = row
    await bus.publish("activity.ran", to_dict(a), org_id=a.org_id)
    return [x.id for x in agents]


async def tick() -> int:
    """Run every enabled activity that's due (in running orgs)."""
    from app.models import Org

    now = utcnow()
    async with SessionLocal() as session:
        due = (await session.execute(select(Activity.id).join(Org, Org.id == Activity.org_id)
                                     .where(Activity.enabled.is_(True),
                                            Activity.next_run_at.is_not(None),
                                            Activity.next_run_at <= now,
                                            Org.status == "running"))).scalars().all()
    for aid in due:
        try:
            await run(aid)
        except Exception:  # noqa: BLE001 - one broken activity must not stop the rest
            log.exception("activity %s failed", aid)
    return len(due)
