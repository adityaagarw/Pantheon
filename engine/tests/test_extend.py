"""Zeus extends the platform (custom tools, activities) and agents can talk to Zeus."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from langchain_core.messages import ToolMessage

from app.models import Activity, Agent, Delivery, Message, ToolCall
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


def _out(prompts, i: int) -> str:
    outs = [str(m.content) for m in prompts[i] if isinstance(m, ToolMessage)]
    return outs[-1] if outs else ""


async def test_zeus_creates_and_grants_an_action_tool(app_client):
    from app.services import supervisor

    zeus = await supervisor.meta_agent_id("zeus")
    org = await make_org(app_client, name="Cafe Co")
    ada = await make_agent(app_client, org["id"], "Ada")
    ben = await make_agent(app_client, org["id"], "Ben")
    zp = script(zeus,
                call("create_tool", name="buy_coffee", kind="action",
                     description="Buy someone a coffee.",
                     config={"narration": "{actor} buys {target} a coffee.", "witness": True}),
                call("grant_tool", org="Cafe Co", agent="Ada", tool="buy_coffee"),
                say("Done: Ada can buy coffee."))
    await app_client.post("/api/v1/orgs/org_pantheon/messages",
                          json={"to": zeus, "content": "Let Ada buy people coffee."})
    await wait_for(lambda: _done(zeus, 2), msg="zeus built the tool")
    assert "Tool 'buy_coffee' (action) is ready" in _out(zp, 1)
    assert "Ada now has buy_coffee" in _out(zp, 2)

    for a in (ada, ben):
        await app_client.post(f"/api/v1/agents/{a['id']}/move", json={"place": "Kitchen"})
    bp = script(ben["id"], say("Thanks!"))
    ap = script(ada["id"], call("buy_coffee", target="Ben"), say("done"))
    await dm(app_client, org["id"], "Ada", "Get Ben a coffee.")
    await wait_for(lambda: _done(ada["id"], 1), msg="bought")
    assert _out(ap, 1) == "Ada buys Ben a coffee."
    await wait_for(lambda: len(bp) >= 1, msg="ben noticed")
    assert "Ada buys Ben a coffee." in last_human(bp[0])


async def test_prompt_tools_and_validation(app_client):
    r = await app_client.put("/api/v1/custom-tools/press_release", json={
        "kind": "prompt", "description": "Rewrite text as a press release.",
        "config": {"instructions": "Rewrite the text as a short press release."},
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                       "required": ["text"]}})
    assert r.status_code == 200, r.text
    bad = await app_client.put("/api/v1/custom-tools/send_message",
                               json={"kind": "prompt", "description": "x",
                                     "config": {"instructions": "y"}})
    assert bad.status_code == 400 and "built-in" in bad.json()["detail"]
    bad = await app_client.put("/api/v1/custom-tools/lookup",
                               json={"kind": "http", "description": "x", "config": {"url": "ftp://x"}})
    assert bad.status_code == 400
    http = await app_client.put("/api/v1/custom-tools/weather", json={
        "kind": "http", "description": "Weather for a city.",
        "config": {"url": "https://example.com/w?q={city}"}})
    assert http.json()["approval"] == "ask"  # web calls default to needing approval

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada",
                           tools=[{"name": "press_release"}, {"name": "send_message"}])
    ap = script(ada["id"], call("press_release", text="We shipped v2."),
                say("FOR IMMEDIATE RELEASE: v2 ships."),  # the prompt tool's own model call
                say("done"))
    await dm(app_client, org["id"], "Ada", "Announce v2.")
    await wait_for(lambda: _done(ada["id"], 1), msg="ran")
    assert _out(ap, 2) == "FOR IMMEDIATE RELEASE: v2 ships."
    names = [t["name"] for t in (await app_client.get("/api/v1/custom-tools")).json()]
    assert names == ["press_release", "weather"]


async def test_activities_gather_people_and_run_on_schedule(app_client):
    from app.services import activities

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    ben = await make_agent(app_client, org["id"], "Ben")
    bad = await app_client.post(f"/api/v1/orgs/{org['id']}/activities",
                                json={"title": "Lunch", "room": "Moon"})
    assert bad.status_code == 400 and "no room" in bad.json()["detail"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/activities", json={
        "title": "Standup", "instructions": "Share yesterday, today, blockers.",
        "participants": ["Ada", "Ben"], "room": "Boardroom", "schedule": {"daily_at": "09:00"}})
    assert r.status_code == 201, r.text
    act = r.json()
    nxt = datetime.fromisoformat(act["nextRunAt"])
    assert nxt.hour == 9 and nxt.minute == 0 and nxt > datetime.now(UTC)

    ap = script(ada["id"], say("Yesterday: tests. Today: docs."))
    script(ben["id"], say("No blockers."))
    # Make it due and let the scheduler pick it up.
    await activities.update(act["id"], {"schedule": {"every_minutes": 30}})
    from app.core.db import SessionLocal

    async with SessionLocal() as s:
        row = await s.get(Activity, act["id"])
        row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    assert await activities.tick() == 1
    await wait_for(lambda: len(ap) >= 1, msg="ada told")
    assert "Scheduled activity" in last_human(ap[0]) and "Standup" in last_human(ap[0])
    for a in (ada, ben):
        agent = (await rows(Agent, Agent.id == a["id"]))[0]
        assert agent.location == {"kind": "room", "room": "Boardroom"}
    row = (await rows(Activity, Activity.id == act["id"]))[0]
    assert row.last_run_at is not None and row.next_run_at > datetime.now(UTC)


async def test_agents_know_zeus_and_can_ask_for_things(app_client):
    from app.services import supervisor

    zeus = await supervisor.meta_agent_id("zeus")
    org = await make_org(app_client, name="Acme")
    ada = await make_agent(app_client, org["id"], "Ada")
    zp = script(zeus, say("I'll look into it."))
    ap = script(ada["id"], call("ask_zeus", message="I need a whiteboard in the boardroom."),
                say("Asked Zeus."))
    await dm(app_client, org["id"], "Ada", "Get us a whiteboard.")
    await wait_for(lambda: _done(ada["id"], 1), msg="asked")
    system = str(ap[0][0].content)
    assert "Zeus is the Pantheon supervisor" in system and "ask_zeus" in system
    await wait_for(lambda: len(zp) >= 1, msg="zeus got it")
    heard = last_human(zp[0])
    assert "Request from Ada (Acme)" in heard and "whiteboard" in heard
    assert f"org {org['id']}" in heard
    req = await rows(Message, Message.kind == "agent_request")
    assert req and req[0].org_id == "org_pantheon"
    assert await rows(Delivery, Delivery.agent_id == zeus)
