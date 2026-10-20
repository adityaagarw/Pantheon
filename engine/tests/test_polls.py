"""Polls: agents woken every few seconds, one agent's poll at a time, never queued behind
a busy agent."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.models import Agent, Message, Schedule
from app.services import schedules
from tests.conftest import wait_for
from tests.helpers import make_agent, make_org, rows, say, script


async def _due(*ids: str, ago: int = 1) -> None:
    async with SessionLocal() as session:
        await session.execute(update(Schedule).where(Schedule.id.in_(ids))
                              .values(next_run_at=utcnow() - timedelta(seconds=ago)))
        await session.commit()


async def _get(sid: str) -> Schedule:
    return (await rows(Schedule, Schedule.id == sid))[0]


async def _agent(aid: str) -> Agent:
    return (await rows(Agent, Agent.id == aid))[0]


@pytest.fixture(autouse=True)
def fresh_poller():
    schedules._running.clear()
    yield
    schedules._running.clear()


async def test_one_poll_at_a_time_and_busy_agents_skip(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    bob = await make_agent(app_client, org["id"], "Bob")
    script(ann["id"], say("checked"), then=say("checked"))
    script(bob["id"], say("checked"), then=say("checked"))
    pa = await schedules.create_poll(await _agent(ann["id"]), "Has the board changed?", 5)
    pb = await schedules.create_poll(await _agent(bob["id"]), "Any new files?", 5)
    await _due(pa.id, ago=3)
    await _due(pb.id, ago=1)

    assert await schedules.poll_tick() == ann["id"]  # the earliest due poll
    assert (await _get(pb.id)).runs == 0  # Bob waits his turn: his tick is skipped, not queued
    assert (await _get(pb.id)).next_run_at > utcnow()
    tick = (await rows(Message, Message.kind == "schedule"))[0]
    assert "Has the board changed?" in tick.content and tick.meta["poll"] is True

    await _due(pb.id)
    assert await schedules.poll_tick() is None  # Ann's tick is still running

    async def ann_idle_again():
        return (await _agent(ann["id"])).runtime_status == "idle"

    await wait_for(ann_idle_again, msg="Ann finished her tick")
    schedules._running["seen_working"] = True
    await _due(pb.id)
    assert await schedules.poll_tick() == bob["id"]
    await wait_for(lambda: _idle(bob["id"]), msg="Bob finished his tick")

    # A busy agent's tick is skipped: no message waits behind the user's.
    schedules._running.clear()
    async with SessionLocal() as session:
        await session.execute(update(Agent).where(Agent.id == ann["id"]).values(runtime_status="working"))
        await session.commit()
    runs = (await _get(pa.id)).runs
    await _due(pa.id)
    assert await schedules.poll_tick() is None
    assert (await _get(pa.id)).runs == runs


async def _idle(agent_id: str) -> bool:
    return (await _agent(agent_id)).runtime_status == "idle"


async def test_polls_end_and_cron_ticks_leave_them_alone(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    p = await schedules.create_poll(await _agent(ann["id"]), "Watch the build", 5, for_minutes=1)
    await _due(p.id)
    assert await schedules.tick() == 0  # the 5-minute ticker doesn't fire polls
    async with SessionLocal() as session:
        await session.execute(update(Schedule).where(Schedule.id == p.id)
                              .values(ends_at=utcnow() - timedelta(seconds=1)))
        await session.commit()
    assert await schedules.poll_tick() is None
    assert (await _get(p.id)).enabled is False


async def test_limits(app_client):
    org = await make_org(app_client)
    ann = await _agent((await make_agent(app_client, org["id"], "Ann"))["id"])
    with pytest.raises(schedules.ScheduleError, match="every 5 seconds"):
        await schedules.create_poll(ann, "too fast", 2)
    with pytest.raises(schedules.ScheduleError, match="for_minutes"):
        await schedules.create_poll(ann, "too long", 10, for_minutes=5000)
    for i in range(3):
        await schedules.create_poll(ann, f"watch {i}", 10)
    with pytest.raises(schedules.ScheduleError, match="already have 3 polls"):
        await schedules.create_poll(ann, "one more", 10)
