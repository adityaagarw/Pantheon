"""Tasks, MCP tools, meetings, the supervisor loop, compaction and crash recovery."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from langchain_core.messages import AIMessage

from app.core.db import SessionLocal
from app.llm import mock
from app.models import FeatureRequest, Meeting, Memory, Message, Task, ToolCall, Turn
from app.services.supervisor import SYSTEM_ORG_ID
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script


async def _turns(agent_id: str, status: str = "completed") -> list[Turn]:
    return await rows(Turn, Turn.agent_id == agent_id, Turn.status == status)


async def test_task_lifecycle_notifies_the_right_people(app_client):
    org = await make_org(app_client)
    pm = await make_agent(app_client, org["id"], "Priya", role="PM")
    dev = await make_agent(app_client, org["id"], "Dan", role="Developer")
    qa = await make_agent(app_client, org["id"], "Quinn", role="QA")
    dev_prompts = script(dev["id"], call("update_task", task="T-2", status="in_progress"),
                         call("update_task", task="T-2", status="review",
                              result="Built the thing"),
                         say("Sent for review"), then=say("noted"))
    qa_prompts = script(qa["id"], then=say("noted"))
    pm_prompts = script(
        pm["id"],
        call("create_task", title="Ship feature", description="The whole feature"),
        call("create_task", title="Build it", assignee="Dan", parent="T-1", reviewer="Priya",
             acceptance="Works"),
        call("create_task", title="Test it", assignee="Quinn", depends_on=["T-2"],
             parent="T-1"),
        say("Planned."),
        # Review of T-2:
        call("update_task", task="T-2", status="done", comment="LGTM"),
        say("Approved"),
        then=say("noted"),
    )
    await dm(app_client, org["id"], "Priya", "Plan the feature")

    # Dan got the assignment and moved it to review -> Priya reviews -> done.
    await wait_for(lambda: _status_is(org["id"], 2, "done"), timeout=30, msg="T-2 done")
    assert "assigned you T-2" in last_human(dev_prompts[0])
    assert any("Please review" in last_human(p) for p in pm_prompts)
    # Quinn was told about the assignment (with the dependency) and later unblocked.
    await wait_for(lambda: any("can start it now" in last_human(p) for p in qa_prompts),
                   timeout=30, msg="unblock notice")
    assert "waiting on: T-2" in last_human(qa_prompts[0])

    r = await app_client.get(f"/api/v1/orgs/{org['id']}/tasks")
    by_num = {t["number"]: t for t in r.json()}
    assert by_num[2]["result"] == "Built the thing"
    assert by_num[2]["parentId"] == by_num[1]["id"]
    detail = (await app_client.get(f"/api/v1/tasks/{by_num[2]['id']}")).json()
    kinds = [e["kind"] for e in detail["timeline"]]
    assert kinds[0] == "created" and "status" in kinds and "comment" in kinds


async def _status_is(org_id: str, number: int, status: str) -> bool:
    ts = await rows(Task, Task.org_id == org_id, Task.number == number)
    return bool(ts) and ts[0].status == status


async def test_user_can_manage_tasks_over_the_api(app_client):
    org = await make_org(app_client)
    dev = await make_agent(app_client, org["id"], "Dev")
    script(dev["id"], then=say("ack"))
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/tasks",
                              json={"title": "Fix bug", "assigneeId": dev["id"]})
    assert r.status_code == 201 and r.json()["status"] == "todo"
    tid = r.json()["id"]
    await wait_for(lambda: _turns(dev["id"]), msg="dev notified")
    r = await app_client.patch(f"/api/v1/tasks/{tid}", json={"status": "blocked",
                                                              "comment": "waiting on infra"})
    assert r.json()["status"] == "blocked"
    r = await app_client.patch(f"/api/v1/tasks/{tid}", json={"status": "nonsense"})
    assert r.status_code == 400


async def test_mcp_stdio_server_tools(app_client):
    server = Path(__file__).parent / "fixtures" / "mcp_echo.py"
    r = await app_client.post("/api/v1/mcp-servers", json={
        "name": "echo", "transport": "stdio", "command": sys.executable,
        "args": [str(server)]})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = await app_client.post(f"/api/v1/mcp-servers/{sid}/test")
    assert r.json()["ok"], r.json()
    assert {t["name"] for t in r.json()["tools"]} == {"echo", "add"}

    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Echoer",
                         tools=[{"name": "mcp:echo:*", "approval": "auto"}])
    prompts = script(a["id"], call("mcp__echo__echo", text="hello mcp"),
                     call("mcp__echo__add", a=2, b=3), say("done"))
    await dm(app_client, org["id"], "Echoer", "use echo")
    await wait_for(lambda: _turns(a["id"]), timeout=60, msg="turn")
    results = [str(m.content) for m in prompts[-1] if m.type == "tool"]
    assert results[0] == "HELLO MCP"
    assert results[1].strip() == "5"
    tcs = await rows(ToolCall, ToolCall.agent_id == a["id"])
    assert {t.status for t in tcs} == {"ok"}


async def test_broken_mcp_server_does_not_break_the_agent(app_client):
    r = await app_client.post("/api/v1/mcp-servers", json={
        "name": "broken", "transport": "stdio", "command": "definitely-not-a-real-binary"})
    assert r.status_code == 201
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Resilient",
                         tools=[{"name": "mcp:broken:*"}, {"name": "send_message"}])
    prompts = script(a["id"], say("still fine"))
    await dm(app_client, org["id"], "Resilient", "hi")
    await wait_for(lambda: _turns(a["id"]), timeout=90, msg="turn")
    system = str(prompts[0][0].content)
    assert "MCP server 'broken' is unavailable" in system


async def test_feature_request_reaches_supervisor_who_answers(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Needy")
    sup = (await app_client.get("/api/v1/supervisor")).json()["agent"]
    script(a["id"], call("request_feature", title="Need Jira access",
                         description="I track work in Jira", rationale="team uses Jira"),
           say("asked"), then=say("thanks"))
    sup_prompts = script(sup["id"], lambda msgs, tools: _answer_fr(msgs, org["id"]),
                         say("handled"), then=say("ok"))
    await dm(app_client, org["id"], "Needy", "get jira")
    await wait_for(lambda: _fr_status("accepted"), timeout=30, msg="triaged")
    assert "Need Jira access" in last_human(sup_prompts[0])
    replies = await wait_for(lambda: rows(Message, Message.kind == "supervisor"),
                             msg="supervisor reply")
    assert replies[0].org_id == org["id"]
    await wait_for(lambda: _turns(a["id"], "completed"), msg="agent got reply")


def _answer_fr(messages, org_id):
    import re

    fr_id = re.search(r"fr_[0-9a-z]+", last_human(messages)).group(0)
    return AIMessage(content="", tool_calls=[
        {"name": "update_feature_request", "args": {"id": fr_id, "status": "accepted",
                                                    "resolution": "Will add Jira MCP"},
         "id": "sup1"},
        {"name": "message_agent", "args": {"org": org_id, "agent": "Needy",
                                           "content": "Jira MCP coming"}, "id": "sup2"},
    ])


async def _fr_status(status: str) -> bool:
    frs = await rows(FeatureRequest)
    return bool(frs) and frs[0].status == status


async def test_supervisor_can_build_an_org(app_client):
    sup = (await app_client.get("/api/v1/supervisor")).json()
    script(sup["agent"]["id"],
           call("create_org", name="Studio", description="A tiny studio"),
           call("create_agent", org="Studio", name="Lena", role="Lead",
                persona="You lead the studio."),
           call("create_agent", org="Studio", name="Omar", role="Engineer",
                persona="You build things.", manager="Lena",
                tools=[{"name": "read_file"}, {"name": "run_command", "approval": "ask"}]),
           say("Built Studio with Lena and Omar."))
    r = await app_client.post(f"/api/v1/orgs/{SYSTEM_ORG_ID}/messages",
                              json={"to": sup["agent"]["id"], "content": "Make me a studio"})
    assert r.status_code == 201
    await wait_for(lambda: _turns(sup["agent"]["id"]), timeout=30, msg="supervisor turn")
    orgs_ = (await app_client.get("/api/v1/orgs")).json()
    studio = next(o for o in orgs_ if o["name"] == "Studio")
    snap = (await app_client.get(f"/api/v1/orgs/{studio['id']}")).json()
    names = {a["name"]: a for a in snap["agents"]}
    assert set(names) == {"Lena", "Omar"}
    assert snap["relationships"][0]["kind"] == "manages"
    omar_tools = {t["name"]: t["approval"] for t in names["Omar"]["tools"]}
    assert omar_tools == {"read_file": "auto", "run_command": "ask"}
    # A regular agent cannot see admin tools.
    assert "create_org" not in {t["name"] for t in names["Lena"]["tools"]}


async def test_meeting_produces_minutes_and_notifies(app_client):
    org = await make_org(app_client)
    lead = await make_agent(app_client, org["id"], "Lead")
    b = await make_agent(app_client, org["id"], "Bea")
    c = await make_agent(app_client, org["id"], "Cal")
    speech = {"n": 0}

    def talk(messages, tools):
        text = last_human(messages)
        if "Agenda:" in text and "Your turn" in text:
            speech["n"] += 1
            return say(f"Point {speech['n']}")
        if "Attendees:" in text:
            return say("Decisions:\n- Use Postgres\nAction items:\n- Bea — write schema")
        return None

    def lead_fn(messages, tools):
        r = talk(messages, tools)
        if r:
            return r
        if not any(m.type == "tool" for m in messages):
            return call("hold_meeting", participants=["Bea", "Cal"], agenda="Pick a database",
                        rounds=1)
        return say("Meeting done")

    def member_fn(messages, tools):
        return talk(messages, tools) or say("noted")

    mock.set_script(lead["id"], lead_fn)
    mock.set_script(b["id"], member_fn)
    mock.set_script(c["id"], member_fn)
    await dm(app_client, org["id"], "Lead", "decide the db")
    meetings = await wait_for(lambda: rows(Meeting, Meeting.status == "done"), timeout=30,
                              msg="meeting")
    assert "Use Postgres" in meetings[0].minutes
    assert speech["n"] == 3
    mems = await rows(Memory, Memory.kind == "meeting")
    assert mems and "Bea — write schema" in mems[0].content
    await wait_for(lambda: _turns(b["id"]), msg="Bea read minutes")


async def test_compaction_summarizes_old_context(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Chatty", model={"context_window": 3000})

    def fn(messages, tools):
        text = last_human(messages)
        if "running working-memory summary" in str(messages[0].content):
            return say("SUMMARY: user sent many long messages")
        return say("ok " + text[-20:])

    mock.set_script(a["id"], fn)
    for i in range(12):
        await dm(app_client, org["id"], "Chatty", f"message {i} " + ("lorem ipsum " * 60))
        await wait_for(lambda i=i: _turns(a["id"]), msg="turn")
        await wait_for(lambda i=i: _all_done(a["id"]), msg="idle")
    thread = (await app_client.get(f"/api/v1/agents/{a['id']}/thread")).json()
    assert thread["summary"].startswith("SUMMARY")
    assert len(thread["messages"]) < 24
    assert thread["messages"][0]["role"] == "user"


async def _all_done(agent_id: str) -> bool:
    from app.models import Delivery

    return not await rows(Delivery, Delivery.agent_id == agent_id, Delivery.status != "done")


async def test_crash_mid_turn_resumes_without_repeating_tools(db, workspace_root):
    """Kill the runtime mid-turn; a fresh runtime resumes from the checkpoint."""
    from httpx import ASGITransport, AsyncClient

    from app.agents import runtime as runtime_mod
    from app.main import app

    hold = asyncio.Event()
    count = {"model": 0}

    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            org = await make_org(client)
            a = await make_agent(client, org["id"], "Survivor")

            def fn(messages, tools):
                count["model"] += 1
                if not any(m.type == "tool" for m in messages):
                    return call("write_file", path="once.txt", content="x")
                if not hold.is_set():
                    raise RuntimeError("simulated crash after the tool ran")
                return say("finished")

            mock.set_script(a["id"], fn)
            await dm(client, org["id"], "Survivor", "write once.txt")
            await wait_for(lambda: rows(ToolCall, ToolCall.status == "ok"), msg="tool ran")
            await wait_for(lambda: rows(Turn, Turn.status == "failed"), msg="failed turn")
    # "Process restart": new runtime instance, same database.
    runtime_mod.runtime = runtime_mod.Runtime()
    import app.main as main_mod

    main_mod.runtime = runtime_mod.runtime
    hold.set()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
            await wait_for(lambda: rows(Turn, Turn.status == "completed"), timeout=30,
                           msg="resumed turn")
    tcs = await rows(ToolCall, ToolCall.name == "write_file")
    assert len(tcs) == 1  # never executed twice
    assert len(list(workspace_root.rglob("once.txt"))) == 1


_ = SessionLocal
