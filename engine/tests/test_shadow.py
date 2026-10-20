"""Shadowing: this computer is recorded while the user works, the agent learns it and keeps a
playbook. The recorder is faked here (a real one would record the test machine)."""

from __future__ import annotations

import asyncio

import pytest

from app.models import Memory, Playbook
from tests.conftest import wait_for
from tests.helpers import call, last_human, make_agent, make_org, rows, say, script

# a 1x1 PNG
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
         "YAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
SECRET = "sk-live-0123456789abcdef"
EVENTS = [
    {"t": 0.5, "kind": "window", "data": {"title": "Invoices - Billing"}},
    {"t": 0.6, "kind": "screen", "image": PIXEL, "data": {"window": "Invoices - Billing"}},
    {"t": 4, "kind": "click", "data": {"x": 120, "y": 340, "button": "left",
                                       "window": "Invoices - Billing"}},
    {"t": 9, "kind": "typing", "data": {"keys": 9, "special": ["tab"],
                                        "window": f"API key {SECRET}"}},
    {"t": 16, "kind": "bogus", "data": {}},
]


@pytest.fixture
def fake_recorder(monkeypatch):
    """The recorder hands over EVENTS, then waits like a real one until it's stopped."""
    from app.computer import recorder

    state = {"running": 0}

    async def record(add):
        state["running"] += 1
        try:
            await add(EVENTS)
            await asyncio.Event().wait()
        finally:
            state["running"] -= 1

    monkeypatch.setattr(recorder, "record", record)
    return state


async def test_record_this_computer_learn_and_keep_a_playbook(app_client, fake_recorder):
    org = await make_org(app_client)
    mia = await make_agent(app_client, org["id"], "Mia")
    await app_client.post(f"/api/v1/orgs/{org['id']}/secrets", json={
        "name": "STRIPE_KEY", "value": SECRET, "agents": ["Mia"], "domains": ["api.stripe.com"]})

    r = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": mia["id"], "title": "Weekly invoice run", "note": "Every Monday morning"})
    assert r.status_code == 201, r.text
    rec = r.json()
    assert rec["source"] == "computer" and rec["status"] == "recording"
    await wait_for(lambda: _count(app_client, rec["id"], 4), msg="events recorded")
    assert fake_recorder["running"] == 1

    # One recording at a time on this computer.
    again = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": mia["id"], "title": "Another"})
    assert again.status_code == 400 and "already being recorded" in again.json()["detail"]

    detail = (await app_client.get(f"/api/v1/shadow/{rec['id']}")).json()
    assert detail["eventCount"] == 4 and detail["agentName"] == "Mia"
    assert SECRET not in str(detail["events"])
    img = next(e["image"] for e in detail["events"] if e.get("image"))
    assert (await app_client.get(img)).headers["content-type"] == "image/png"

    prompts = script(
        mia["id"],
        call("shadow_review", session=rec["id"]),
        call("save_playbook", session=rec["id"], title="Weekly invoice run",
             content="1. Open Invoices in Billing\n2. New invoice for each customer\n3. Send it"),
        say("Learned it."), then=say("ok"))
    learned = await app_client.post(f"/api/v1/shadow/{rec['id']}/learn")
    assert learned.json()["status"] == "learning"
    assert fake_recorder["running"] == 0  # learning stopped the recording

    await wait_for(lambda: rows(Playbook), timeout=30, msg="playbook saved")
    assert "Weekly invoice run" in last_human(prompts[0])
    assert "on their computer" in last_human(prompts[0])
    timeline = str([m for m in prompts[1] if m.type == "tool"][0].content)
    assert "[screenshot 1]" in timeline and SECRET not in timeline

    after = (await app_client.get(f"/api/v1/orgs/{org['id']}/shadow")).json()[0]
    assert after["status"] == "learned"
    books = (await app_client.get(f"/api/v1/orgs/{org['id']}/playbooks")).json()
    assert books[0]["title"] == "Weekly invoice run" and books[0]["agentId"] == mia["id"]
    assert any(m.kind == "playbook" for m in await rows(Memory, Memory.agent_id == mia["id"]))


async def _count(client, session_id: str, n: int) -> bool:
    return (await client.get(f"/api/v1/shadow/{session_id}")).json()["eventCount"] >= n


async def test_an_empty_recording_and_deleting(app_client, monkeypatch):
    from app.computer import recorder

    async def nothing(add):
        await asyncio.Event().wait()

    monkeypatch.setattr(recorder, "record", nothing)
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    first = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": ann["id"], "title": "Triage"})
    assert first.status_code == 201
    empty = await app_client.post(f"/api/v1/shadow/{first.json()['id']}/learn")
    assert empty.status_code == 400 and "nothing was recorded" in empty.json()["detail"]
    await app_client.delete(f"/api/v1/shadow/{first.json()['id']}")
    assert (await app_client.get(f"/api/v1/orgs/{org['id']}/shadow")).json() == []
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/shadow", json={
        "agentId": ann["id"], "title": "Elsewhere", "source": "browser"})
    assert r.status_code == 400  # this computer only
