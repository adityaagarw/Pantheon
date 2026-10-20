"""Agents' own timers: cron expressions, one-off reminders, and polls.

An agent schedules a note to itself ("0 9 * * 1-5: check the overnight
build"); when it's due, the note arrives in the agent's inbox and wakes it.
Cron expressions are evaluated in the schedule's timezone (default UTC).

A **poll** wakes its agent every few seconds for a while ("every 10 s for 30 minutes:
has the user moved on the whiteboard?"). Polls fire only when their agent is idle (a
tick it can't take is skipped, so they never pile up in front of the user's messages),
and only one agent's poll runs at a time across Pantheon (run_polls_forever).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Agent, Org, Schedule

log = logging.getLogger(__name__)
MIN_GAP = timedelta(minutes=5)
MAX_PER_AGENT = 50
MIN_POLL_SECONDS = 5
MAX_POLL_MINUTES = 24 * 60
MAX_POLLS_PER_AGENT = 3


class ScheduleError(ValueError):
    pass


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        raise ScheduleError(f"unknown timezone '{name}' (use e.g. UTC, Europe/London, "
                            "America/New_York, Asia/Kolkata)") from None


def next_cron(expr: str, tz: str, after: datetime) -> datetime:
    it = croniter(expr, after.astimezone(_tz(tz)))
    return it.get_next(datetime)


def validate_cron(expr: str, tz: str) -> None:
    if not croniter.is_valid(expr):
        raise ScheduleError(f"'{expr}' is not a valid cron expression "
                            "(minute hour day-of-month month day-of-week, e.g. '0 9 * * 1-5')")
    first = next_cron(expr, tz, utcnow())
    second = next_cron(expr, tz, first)
    if second - first < MIN_GAP:
        raise ScheduleError("schedules can repeat at most every 5 minutes")


def to_dict(s: Schedule) -> dict[str, Any]:
    return {"id": s.id, "orgId": s.org_id, "agentId": s.agent_id, "message": s.message,
            "cron": s.cron, "timezone": s.timezone, "enabled": s.enabled, "runs": s.runs,
            "createdBy": s.created_by, "everySeconds": s.every_seconds,
            "endsAt": s.ends_at.isoformat() if s.ends_at else None,
            "nextRunAt": s.next_run_at.isoformat() if s.next_run_at else None,
            "lastRunAt": s.last_run_at.isoformat() if s.last_run_at else None}


async def create(agent: Agent, message: str, *, cron: str | None = None,
                 at: datetime | None = None, timezone: str = "UTC",
                 created_by: str = "") -> Schedule:
    if not message.strip():
        raise ScheduleError("say what the reminder is for")
    now = utcnow()
    _tz(timezone)
    if cron:
        validate_cron(cron, timezone)
        first = next_cron(cron, timezone, now)
    elif at is not None:
        if at.tzinfo is None:
            at = at.replace(tzinfo=_tz(timezone))
        if at <= now:
            raise ScheduleError("that time is in the past")
        first = at
    else:
        raise ScheduleError("give a cron expression, a time (at) or in_minutes")
    async with SessionLocal() as session:
        count = len((await session.execute(select(Schedule.id).where(
            Schedule.agent_id == agent.id, Schedule.enabled.is_(True)))).all())
        if count >= MAX_PER_AGENT:
            raise ScheduleError(f"you already have {count} schedules; cancel some first")
        s = Schedule(org_id=agent.org_id, agent_id=agent.id, message=message.strip(),
                     cron=cron or None, timezone=timezone or "UTC", next_run_at=first,
                     created_by=created_by or agent.name)
        session.add(s)
        await session.commit()
    await bus.publish("schedule.saved", to_dict(s), org_id=agent.org_id, agent_id=agent.id)
    return s


async def for_agent(agent_id: str, *, include_done: bool = False) -> list[Schedule]:
    async with SessionLocal() as session:
        stmt = select(Schedule).where(Schedule.agent_id == agent_id)
        if not include_done:
            stmt = stmt.where(Schedule.enabled.is_(True))
        return list((await session.execute(stmt.order_by(Schedule.next_run_at))).scalars())


async def cancel(schedule_id: str, *, agent_id: str | None = None) -> Schedule:
    async with SessionLocal() as session:
        s = await session.get(Schedule, schedule_id)
        if s is None or (agent_id and s.agent_id != agent_id):
            raise ScheduleError(f"no schedule {schedule_id}")
        s.enabled = False
        s.next_run_at = None
        await session.commit()
    await bus.publish("schedule.saved", to_dict(s), org_id=s.org_id, agent_id=s.agent_id)
    return s


async def fire(s: Schedule) -> None:
    from app.services import comms

    when = "recurring: " + s.cron if s.cron else "one-off"
    await comms.notify(s.org_id, [s.agent_id], f"⏰ Reminder you scheduled ({when}):\n{s.message}",
                       kind="schedule", meta={"scheduleId": s.id})


async def tick() -> int:
    now = utcnow()
    async with SessionLocal() as session:
        due = (await session.execute(select(Schedule).join(Org, Org.id == Schedule.org_id).where(
            Schedule.enabled.is_(True), Schedule.next_run_at.is_not(None),
            Schedule.every_seconds.is_(None),  # polls run in run_polls_forever
            Schedule.next_run_at <= now, Org.status == "running"))).scalars().all()
        for s in due:
            s.last_run_at = now
            s.runs += 1
            if s.cron:
                s.next_run_at = next_cron(s.cron, s.timezone, now)
            else:
                s.enabled, s.next_run_at = False, None
        await session.commit()
    for s in due:
        try:
            await fire(s)
            await bus.publish("schedule.saved", to_dict(s), org_id=s.org_id,
                              agent_id=s.agent_id)
        except Exception:  # noqa: BLE001 - one bad schedule must not stop the rest
            log.exception("schedule %s failed", s.id)
    return len(due)


# --- polls --------------------------------------------------------------------------------


async def create_poll(agent: Agent, message: str, every_seconds: int, for_minutes: int = 30,
                      created_by: str = "") -> Schedule:
    if not message.strip():
        raise ScheduleError("say what to check on each poll")
    if every_seconds < MIN_POLL_SECONDS:
        raise ScheduleError(f"polls run at most every {MIN_POLL_SECONDS} seconds")
    if not 1 <= for_minutes <= MAX_POLL_MINUTES:
        raise ScheduleError(f"for_minutes must be 1 to {MAX_POLL_MINUTES}")
    now = utcnow()
    async with SessionLocal() as session:
        count = len((await session.execute(select(Schedule.id).where(
            Schedule.agent_id == agent.id, Schedule.enabled.is_(True),
            Schedule.every_seconds.is_not(None)))).all())
        if count >= MAX_POLLS_PER_AGENT:
            raise ScheduleError(f"you already have {count} polls; cancel one first")
        s = Schedule(org_id=agent.org_id, agent_id=agent.id, message=message.strip(),
                     every_seconds=every_seconds, ends_at=now + timedelta(minutes=for_minutes),
                     next_run_at=now + timedelta(seconds=every_seconds),
                     created_by=created_by or agent.name)
        session.add(s)
        await session.commit()
    await bus.publish("schedule.saved", to_dict(s), org_id=agent.org_id, agent_id=agent.id)
    return s


# The poll whose agent is working on its tick: nobody else's poll fires meanwhile.
_running: dict[str, Any] = {}
POLL_LOOP_SECONDS = 1.0


async def poll_tick() -> str | None:
    """Fire at most one due poll. Returns the agent id it woke, if any."""
    import time

    now = utcnow()
    async with SessionLocal() as session:
        if _running:
            agent = await session.get(Agent, _running["agent"])
            status = agent.runtime_status if agent else "idle"
            if status != "idle":
                _running["seen_working"] = True
                return None
            if not _running.get("seen_working") and time.monotonic() - _running["at"] < 15:
                return None  # woken, not started yet
            _running.clear()
        due = (await session.execute(
            select(Schedule, Agent.runtime_status).join(Org, Org.id == Schedule.org_id)
            .join(Agent, Agent.id == Schedule.agent_id).where(
                Schedule.enabled.is_(True), Schedule.every_seconds.is_not(None),
                Schedule.next_run_at <= now, Org.status == "running")
            .order_by(Schedule.next_run_at))).all()
        fired = None
        for s, status in due:
            if s.ends_at is not None and now >= s.ends_at:
                s.enabled, s.next_run_at = False, None
                continue
            s.next_run_at = now + timedelta(seconds=s.every_seconds)
            if status != "idle" or fired is not None:
                continue  # busy (or another poll goes first): skip this tick, don't queue it
            s.last_run_at = now
            s.runs += 1
            fired = s
        await session.commit()
    if fired is None:
        return None
    _running.update(agent=fired.agent_id, at=time.monotonic(), seen_working=False)
    from app.services import comms

    left = int((fired.ends_at - now).total_seconds() // 60) if fired.ends_at else 0
    await comms.notify(fired.org_id, [fired.agent_id],
                       f"🔁 Poll {fired.id} (every {fired.every_seconds} s, about {left} min "
                       f"left): {fired.message}\nCheck, act if needed, and end your turn "
                       "quickly. cancel_schedule when it's no longer needed.",
                       kind="schedule", meta={"scheduleId": fired.id, "poll": True})
    await bus.publish("schedule.saved", to_dict(fired), org_id=fired.org_id,
                      agent_id=fired.agent_id)
    return fired.agent_id


async def run_polls_forever() -> None:
    import asyncio

    while True:
        try:
            await asyncio.sleep(POLL_LOOP_SECONDS)
            await poll_tick()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the poller must never die
            log.exception("poll tick failed")

