"""A meeting can't get stuck "in progress" and break the space (Zeus-triaged bug)."""

from __future__ import annotations

import pytest

from app.core.db import SessionLocal
from app.models import Meeting
from app.services import meetings, spatial
from app.tools import base
from tests.helpers import make_agent, make_org, rows


async def _running(org_id: str, agent_ids: list[str], room: str | None) -> str:
    async with SessionLocal() as session:
        m = Meeting(org_id=org_id, facilitator_id=agent_ids[0], participants=agent_ids,
                    agenda="Standup", options={"room": room} if room else {}, status="running")
        session.add(m)
        await session.commit()
        return m.id


async def test_a_meeting_in_progress_does_not_crash_the_space(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    bob = await make_agent(app_client, org["id"], "Bob")
    await _running(org["id"], [ada["id"], bob["id"]], "Meeting Room")
    # Used to raise: 'Meeting' object has no attribute 'room'.
    assert await spatial._meeting_rooms(org["id"]) == {ada["id"]: "Meeting Room",
                                                      bob["id"]: "Meeting Room"}


async def test_a_failing_meeting_is_closed_not_left_running(app_client, monkeypatch):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    bob = await make_agent(app_client, org["id"], "Bob")

    async def broken(*a, **k):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(meetings, "_minutes", broken)
    async with SessionLocal() as session:
        from app.models import Agent, Org

        o, fa, pb = await session.get(Org, org["id"]), await session.get(Agent, ada["id"]), \
            await session.get(Agent, bob["id"])
    req = await meetings.prepare(o, fa, [pb.id], "Quick sync", style="standup", rounds=1,
                                 called_by="user")
    with pytest.raises(RuntimeError):
        await meetings.run(req)
    [m] = await rows(Meeting, Meeting.org_id == org["id"])
    assert m.status == "failed" and "something unexpected" in m.minutes


async def test_restart_sweep_and_manual_end(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    stale = await _running(org["id"], [ada["id"]], None)
    assert await meetings.sweep_interrupted() == 1
    [m] = await rows(Meeting, Meeting.id == stale)
    assert m.status == "failed" and "restarted" in m.minutes

    stuck = await _running(org["id"], [ada["id"]], "Boardroom")
    listing = await base.builtin("meetings_in_progress").handler({"org": org["id"]}, None)
    assert stuck in listing and "Boardroom" in listing and "Ada" in listing
    r = await app_client.post(f"/api/v1/meetings/{stuck}/end")
    assert r.status_code == 200 and r.json()["status"] == "failed"
    assert (await app_client.post(f"/api/v1/meetings/{stuck}/end")).status_code == 404

    again = await _running(org["id"], [ada["id"]], None)
    out = await base.builtin("end_meeting").handler({"meeting": again}, None)
    assert "Ended meeting" in out
    assert "No meetings in progress" in await base.builtin("meetings_in_progress").handler(
        {"org": org["id"]}, None)
