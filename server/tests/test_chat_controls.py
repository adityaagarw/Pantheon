"""Clearing a chat, resetting Zeus/Argus/Kronos, and steering an agent mid-turn."""

from __future__ import annotations

from app.models import Agent, Memory, Message, Turn
from app.services import comms
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script


async def _done(agent_id: str, n: int = 1) -> bool:
    return sum(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id)) >= n


async def _dm_messages(agent: dict) -> list[Message]:
    from app.models import Channel

    ch = await rows(Channel, Channel.org_id == agent["orgId"],
                    Channel.key == comms.dm_key(agent["id"], comms.USER))
    return await rows(Message, Message.channel_id == ch[0].id) if ch else []


async def test_clearing_a_chat_removes_the_messages_and_the_context(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    script(ada["id"], say("hello there"), then=say("ok"))
    await dm(app_client, org["id"], "Ada", "hi")
    await wait_for(lambda: _done(ada["id"]), msg="Ada replied")
    assert len(await _dm_messages(ada)) >= 2

    r = await app_client.post(f"/api/v1/agents/{ada['id']}/clear-chat")
    assert r.status_code == 200 and r.json()["removed"] >= 2
    assert await _dm_messages(ada) == []  # no "context cleared" marker either

    prompts = script(ada["id"], say("fresh"), then=say("ok"))
    await dm(app_client, org["id"], "Ada", "who am I?")
    await wait_for(lambda: _done(ada["id"], 2), msg="Ada replied again")
    assert "hi" not in [str(m.content) for m in prompts[0][1:-1]]  # the old exchange is gone


async def test_meta_agents_can_be_reset(app_client):
    zeus = (await app_client.get("/api/v1/meta/zeus")).json()["agent"]
    await app_client.patch(f"/api/v1/agents/{zeus['id']}", json={
        "persona": "You are a pirate.", "model": {"temperature": 1.3}, "limits": {"max_steps_per_turn": 3}})
    script(zeus["id"], say("arr"), then=say("ok"))
    await dm(app_client, zeus["orgId"], zeus["id"], "hello")
    await wait_for(lambda: _done(zeus["id"]), msg="Zeus replied")
    from app.core.db import SessionLocal

    async with SessionLocal() as session:
        session.add(Memory(org_id=zeus["orgId"], agent_id=zeus["id"], content="the user likes tea"))
        await session.commit()

    kept = (await app_client.post("/api/v1/meta/zeus/reset", json={})).json()["agent"]
    assert kept["persona"] != "You are a pirate." and "Zeus" in kept["persona"]
    assert kept["model"] == {} and kept["limits"] == {}
    assert await _dm_messages(kept) == []
    assert await rows(Memory, Memory.agent_id == zeus["id"])  # memories stay unless asked

    await app_client.post("/api/v1/meta/zeus/reset", json={"forget": True})
    assert await rows(Memory, Memory.agent_id == zeus["id"]) == []
    assert (await app_client.post("/api/v1/meta/hermes/reset")).status_code == 404
    for role in ("argus", "kronos"):
        assert (await app_client.post(f"/api/v1/meta/{role}/reset")).status_code == 200


async def test_the_user_can_steer_a_working_agent(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    await app_client.patch(f"/api/v1/agents/{ada['id']}", json={"tools": [
        {"name": "run_command", "approval": "auto"}]})

    def after_command(messages, tools):
        return say("noted: " + last_human(messages)[-60:])

    prompts = script(ada["id"],
                     call("run_command", command="python -c \"import time; time.sleep(4)\""),
                     after_command, then=say("ok"))
    await dm(app_client, org["id"], "Ada", "count the files")
    await wait_for(lambda: _working(ada["id"]), msg="Ada working")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/messages",
                              json={"to": "Ada", "content": "actually, only the .py files",
                                    "steer": True})
    assert r.status_code == 201
    await wait_for(lambda: _done(ada["id"]), timeout=60, msg="Ada finished")

    # The second model call of the same turn already had the steering message.
    second = prompts[1]
    assert "only the .py files" in last_human(second) and "while you were working" in last_human(second)
    turns = await rows(Turn, Turn.agent_id == ada["id"])
    assert len(turns) == 1  # handled inside the turn, not as a new one
    replies = [m.content for m in await _dm_messages(ada) if m.sender_id == ada["id"]]
    assert any("only the .py files" in c for c in replies)


async def _working(agent_id: str) -> bool:
    return any(a.runtime_status == "working" for a in await rows(Agent, Agent.id == agent_id))


async def test_agents_can_write_in_simplified_technical_english(app_client):
    from app.agents.prompt import STE_LINE, writes_ste

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", persona="You write manuals.\n\n" + STE_LINE)
    bob = await make_agent(app_client, org["id"], "Bob")
    prompts = script(ada["id"], say("Remove the cover."), then=say("ok"))
    await dm(app_client, org["id"], "Ada", "how do I open it?")
    await wait_for(lambda: _done(ada["id"]), msg="Ada replied")
    system = str(prompts[0][0].content)
    assert "# Writing: ASD-STE100" in system and "at most 20 words" in system

    from app.models import Org

    o = (await rows(Org, Org.id == org["id"]))[0]
    b = (await rows(Agent, Agent.id == bob["id"]))[0]
    assert not writes_ste(o, b)
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"writing": "ste"}})
    assert r.status_code == 200
    assert writes_ste((await rows(Org, Org.id == org["id"]))[0], b)  # the whole org now
    bad = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"writing": "pirate"}})
    assert bad.status_code == 400
