"""End-to-end runtime behaviour on the mock model."""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.core.db import SessionLocal
from app.llm import mock
from app.models import (
    Agent,
    Approval,
    Channel,
    Delivery,
    LlmCall,
    Message,
    ToolCall,
    Turn,
    UserInboxItem,
)
from tests.conftest import wait_for
from tests.helpers import call, calls, dm, last_human, make_agent, make_org, rows, say, script


async def _agent(agent_id: str) -> Agent:
    async with SessionLocal() as s:
        return await s.get(Agent, agent_id)


async def _dm_messages(org_id: str, a: str, b: str) -> list[Message]:
    from app.services.comms import dm_key

    async with SessionLocal() as s:
        ch = (await s.execute(select(Channel).where(Channel.org_id == org_id,
                                                    Channel.key == dm_key(a, b)))).scalar_one()
        return list((await s.execute(select(Message).where(Message.channel_id == ch.id)
                                     .order_by(Message.created_at))).scalars())


async def test_user_dm_gets_a_reply_and_everything_is_recorded(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    prompts = script(ada["id"], say("Hi! I'm on it."))
    await dm(app_client, org["id"], "Ada", "Hello Ada, can you help?")

    msgs = await wait_for(lambda: _replies(org["id"], ada["id"]), msg="reply")
    assert msgs[-1].content == "Hi! I'm on it."
    assert msgs[-1].kind == "reply"
    assert "Hello Ada, can you help?" in last_human(prompts[0])

    await wait_for(lambda: _turn_done(ada["id"]), msg="turn completed")
    turns = await rows(Turn, Turn.agent_id == ada["id"])
    assert [t.status for t in turns] == ["completed"]
    calls_ = await rows(LlmCall, LlmCall.turn_id == turns[0].id)
    assert len(calls_) == 1 and calls_[0].request["messages"][0]["role"] == "system"
    await wait_for(lambda: _status(ada["id"], "idle"), msg="idle")
    inbox = await rows(UserInboxItem, UserInboxItem.org_id == org["id"])
    assert any(i.kind == "message" for i in inbox)


async def _replies(org_id: str, agent_id: str) -> list[Message]:
    msgs = await _dm_messages(org_id, agent_id, "user")
    return [m for m in msgs if m.sender_id == agent_id] and msgs


async def test_agents_collaborate_via_messages_and_tools(app_client):
    org = await make_org(app_client)
    lead = await make_agent(app_client, org["id"], "Lead", role="Tech lead")
    dev = await make_agent(app_client, org["id"], "Dev", role="Developer")
    script(lead["id"], call("send_message", to="Dev", content="Please write hello.txt"),
           say("Delegated to Dev."))
    script(dev["id"], call("write_file", path="hello.txt", content="hello world"),
           say("Done: wrote hello.txt"))
    await dm(app_client, org["id"], "Lead", "Get hello.txt written")

    await wait_for(lambda: _turn_done(dev["id"]), msg="dev turn")
    written = await rows(ToolCall, ToolCall.agent_id == dev["id"], ToolCall.name == "write_file")
    assert written[0].status == "ok"
    # Dev's final text auto-replies to Lead (Lead sent an explicit DM).
    msgs = await _dm_messages(org["id"], lead["id"], dev["id"])
    assert [m.content for m in msgs] == ["Please write hello.txt", "Done: wrote hello.txt"]
    # ...but Lead does not auto-reply to an auto-reply (no ping-pong).
    await asyncio.sleep(1.0)
    msgs = await _dm_messages(org["id"], lead["id"], dev["id"])
    assert len(msgs) == 2


async def _turn_done(agent_id: str, n: int = 1) -> bool:
    ts = await rows(Turn, Turn.agent_id == agent_id, Turn.status == "completed")
    return len(ts) >= n


async def test_tool_not_granted_is_refused_and_never_runs(app_client, workspace_root):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Limited", tools=[{"name": "send_message"}])
    prompts = script(a["id"], call("write_file", path="x.txt", content="nope"), say("ok"))
    await dm(app_client, org["id"], "Limited", "write x.txt")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    results = [str(m.content) for m in prompts[1] if m.type == "tool"]
    assert "not available to you" in results[0]
    assert not list(workspace_root.rglob("x.txt"))


async def test_workspace_confinement(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Reader")
    prompts = script(a["id"], call("read_file", path="../../../../etc/passwd"), say("ok"))
    await dm(app_client, org["id"], "Reader", "read it")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    assert "outside your workspace" in [str(m.content) for m in prompts[1] if m.type == "tool"][0]


async def test_invalid_tool_arguments_are_reported_to_the_model(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Sloppy")
    prompts = script(a["id"], call("write_file", path="a.txt"), say("ok"))
    await dm(app_client, org["id"], "Sloppy", "go")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    assert "invalid arguments" in [str(m.content) for m in prompts[1] if m.type == "tool"][0]


async def test_parallel_tool_calls_all_answered(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Multi")
    prompts = script(a["id"], calls(("write_file", {"path": "1.txt", "content": "1"}),
                                    ("write_file", {"path": "2.txt", "content": "2"}),
                                    ("list_dir", {})), say("ok"))
    await dm(app_client, org["id"], "Multi", "go")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    tool_msgs = [m for m in prompts[1] if m.type == "tool"]
    assert len(tool_msgs) == 3


async def test_approval_flow_approve_and_deny(app_client, workspace_root):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Ops")  # run_command defaults to "ask"
    prompts = script(a["id"], call("run_command", command="echo approved-run"),
                     say("ran it"), call("run_command", command="echo second"), say("denied ok"))
    await dm(app_client, org["id"], "Ops", "run echo")

    pending = await wait_for(lambda: rows(Approval, Approval.status == "pending"),
                             msg="approval")
    assert (await _agent(a["id"])).runtime_status == "awaiting_approval"
    r = await app_client.post(f"/api/v1/approvals/{pending[0].id}", json={"approved": True})
    assert r.status_code == 200
    await wait_for(lambda: _turn_done(a["id"]), msg="turn after approval")
    out = [str(m.content) for m in prompts[1] if m.type == "tool"][0]
    assert "approved-run" in out

    await dm(app_client, org["id"], "Ops", "again")
    pending = await wait_for(lambda: rows(Approval, Approval.status == "pending"),
                             msg="second approval")
    await app_client.post(f"/api/v1/approvals/{pending[0].id}",
                          json={"approved": False, "note": "not now"})
    await wait_for(lambda: _turn_done(a["id"], 2), msg="second turn")
    out = [str(m.content) for m in prompts[3] if m.type == "tool"][-1]
    assert out.startswith("DENIED") and "not now" in out


async def test_failures_retry_then_park_then_manual_retry(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Flaky")
    state = {"fail": 10, "calls": 0}

    def fn(messages, tools):
        state["calls"] += 1
        if state["fail"] > 0:
            state["fail"] -= 1
            raise ValueError("model exploded")  # non-transient: fails the turn at once
        return say("recovered")

    mock.set_script(a["id"], fn)
    await dm(app_client, org["id"], "Flaky", "hello")
    agent = await wait_for(lambda: _status(a["id"], "error"), timeout=30, msg="parked")
    assert "model exploded" in agent.runtime_detail
    items = await rows(UserInboxItem, UserInboxItem.kind == "error")
    assert items and "Flaky" in items[0].title

    state["fail"] = 0
    r = await app_client.post(f"/api/v1/agents/{a['id']}/retry")
    assert r.status_code == 200
    await wait_for(lambda: _turn_done(a["id"]), msg="recovered turn")
    # The single inbox message was injected exactly once across all attempts.
    thread = (await app_client.get(f"/api/v1/agents/{a['id']}/thread")).json()
    humans = [m for m in thread["messages"] if m["role"] == "user"]
    assert len(humans) == 1
    assert (await _agent(a["id"])).consecutive_failures == 0


async def _status(agent_id: str, status: str):
    a = await _agent(agent_id)
    return a if a.runtime_status == status else None


async def test_message_chain_depth_limit_stops_runaway_loops(app_client):
    org = await make_org(app_client, settings={"max_chain_depth": 4})
    ping = await make_agent(app_client, org["id"], "Ping")
    pong = await make_agent(app_client, org["id"], "Pong")
    script(ping["id"], then=call("send_message", to="Pong", content="ping"))
    script(pong["id"], then=call("send_message", to="Ping", content="pong"))
    # Each call loop would never end on its own; step limit ends each turn.
    for aid in (ping["id"], pong["id"]):
        await app_client.patch(f"/api/v1/agents/{aid}", json={"limits": {"max_steps_per_turn": 1}})
    await dm(app_client, org["id"], "Ping", "start")
    await wait_for(lambda: rows(Delivery, Delivery.status == "dropped"), timeout=30,
                   msg="dropped delivery")
    await asyncio.sleep(1.5)
    before = len(await rows(Message, Message.org_id == org["id"]))
    await asyncio.sleep(1.5)
    after = len(await rows(Message, Message.org_id == org["id"]))
    assert before == after  # the loop has stopped
    depths = [m.depth for m in await rows(Message, Message.org_id == org["id"])]
    assert max(depths) <= 5


async def test_stop_turn_closes_thread_cleanly(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Stoppable")
    script(a["id"], call("run_command", command="echo hi"), then=say("fresh start"))
    await dm(app_client, org["id"], "Stoppable", "run it")
    await wait_for(lambda: rows(Approval, Approval.status == "pending"), msg="approval")
    r = await app_client.post(f"/api/v1/agents/{a['id']}/stop")
    assert r.json()["stopped"] is True
    assert (await rows(Approval, Approval.agent_id == a["id"]))[0].status == "denied"
    thread = (await app_client.get(f"/api/v1/agents/{a['id']}/thread")).json()
    assert thread["next"] == []
    # The agent keeps working normally afterwards (transcript stayed valid).
    await dm(app_client, org["id"], "Stoppable", "hello again")
    await wait_for(lambda: _turn_done(a["id"]), msg="next turn")


async def test_step_limit_forces_wrap_up(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Busy", limits={"max_steps_per_turn": 3})
    prompts = script(a["id"], then=call("list_dir"))
    await dm(app_client, org["id"], "Busy", "go")
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    assert len(prompts) == 4  # 3 tool steps + 1 forced final answer
    assert "limit" in last_human(prompts[-1])


async def test_messages_arriving_mid_turn_are_processed_next(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Worker")
    gate = asyncio.Event()
    seen: list[str] = []

    def fn(messages, tools):
        seen.append(last_human(messages))
        return say("ok")

    mock.set_script(a["id"], fn)
    await dm(app_client, org["id"], "Worker", "first")
    await dm(app_client, org["id"], "Worker", "second")
    await dm(app_client, org["id"], "Worker", "third")
    await wait_for(lambda: all(any(w in s for s in seen) for w in ("first", "second", "third")),
                   msg="all messages seen")
    gate.set()
    pending = await rows(Delivery, Delivery.agent_id == a["id"], Delivery.status != "done")
    await wait_for(lambda: _no_pending(a["id"]), msg="deliveries done")
    assert not [d for d in pending if d.status == "dropped"]


async def _no_pending(agent_id: str) -> bool:
    return not await rows(Delivery, Delivery.agent_id == agent_id, Delivery.status != "done")


async def test_paused_agent_and_paused_org_do_not_run(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Sleepy")
    script(a["id"], then=say("awake"))
    await app_client.patch(f"/api/v1/agents/{a['id']}", json={"status": "paused"})
    await dm(app_client, org["id"], "Sleepy", "hi")
    await asyncio.sleep(1.0)
    assert not await rows(Turn, Turn.agent_id == a["id"])
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"status": "paused"})
    await app_client.patch(f"/api/v1/agents/{a['id']}", json={"status": "active"})
    await asyncio.sleep(1.0)
    assert not await rows(Turn, Turn.agent_id == a["id"])
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"status": "running"})
    await wait_for(lambda: _turn_done(a["id"]), msg="turn after resume")


async def test_always_allow_switches_tool_to_auto(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Builder")
    script(a["id"], call("run_command", command="echo one"), say("ok"),
           call("run_command", command="echo two"), say("ok again"))
    await dm(app_client, org["id"], "Builder", "go")
    pending = await wait_for(lambda: rows(Approval, Approval.status == "pending"), msg="approval")
    r = await app_client.post(f"/api/v1/approvals/{pending[0].id}",
                              json={"approved": True, "always": True})
    assert r.status_code == 200
    await wait_for(lambda: _turn_done(a["id"]), msg="turn")
    agent = (await app_client.get(f"/api/v1/agents/{a['id']}")).json()
    assert {t["name"]: t["approval"] for t in agent["tools"]}["run_command"] == "auto"
    await dm(app_client, org["id"], "Builder", "again")
    await wait_for(lambda: _turn_done(a["id"], 2), msg="second turn without approval")
    assert len(await rows(Approval, Approval.agent_id == a["id"])) == 1
