# ruff: noqa: E501  (fake web responses)
"""Agents of other kinds: command-line agents, remote agents and people, taking the same
messages and flow steps as Pantheon's own, with Pantheon's tools through the MCP bridge."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

from app.models import Agent, Message, Turn, UserInboxItem
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows

ECHO = """
import os, sys
prompt = sys.stdin.read()
print("working on it")
print("ECHO: " + prompt.strip().splitlines()[-1])
print("SYSTEM-HAS-RULES" if "Working in Pantheon" in prompt else "NO-RULES")
print("BRIDGE" if os.environ.get("PANTHEON_BRIDGE_TOKEN") else "NO-BRIDGE")
"""


async def _completed(agent_id: str, n: int = 1) -> bool:
    return sum(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id)) >= n


async def _replies(agent_id: str) -> list[str]:
    return [m.content for m in await rows(Message, Message.sender_id == agent_id)]


def _plugin():
    return sys.modules["pantheon_pack_agent_kinds"]


async def test_the_kinds_on_offer(app_client):
    listed = {k["id"]: k for k in (await app_client.get("/api/v1/agent-kinds")).json()}
    assert set(listed) >= {"llm", "human", "cli", "remote"}
    assert [p["id"] for p in listed["cli"]["presets"]] == [
        "claude-code", "codex", "hermes", "agy", "opencode", "acp", "custom"]
    assert listed["remote"]["pack"] == "agent-kinds"
    org = await make_org(app_client)
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/agents", json={"name": "X", "kind": "nope"})
    assert r.status_code == 400 and "no agent kind" in r.json()["detail"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/agents", json={
        "name": "Y", "kind": "cli", "runner": {"preset": "custom", "command": ""}})
    assert r.status_code == 400 and "command" in r.json()["detail"]


async def test_a_command_line_agent_answers_like_any_agent(app_client, tmp_path):
    script = tmp_path / "echo_agent.py"
    script.write_text(ECHO, encoding="utf-8")
    org = await make_org(app_client)
    cli = await make_agent(app_client, org["id"], "Cleo", kind="cli",
                           runner={"preset": "custom", "command": str(script)})
    assert cli["kind"] == "cli" and cli["runner"]["preset"] == "custom"
    await dm(app_client, org["id"], "Cleo", "summarise the repo")
    await wait_for(lambda: _completed(cli["id"]), timeout=60, msg="cli turn done")
    reply = (await _replies(cli["id"]))[-1]
    assert "ECHO: summarise the repo" in reply and "SYSTEM-HAS-RULES" in reply and "BRIDGE" in reply
    turn = (await rows(Turn, Turn.agent_id == cli["id"]))[0]
    assert turn.summary.startswith("working on it")

    # Changing its kind clears what the old kind kept; a broken command fails the turn.
    r = await app_client.patch(f"/api/v1/agents/{cli['id']}", json={
        "runner": {"preset": "custom", "command": "definitely-not-installed-xyz"}})
    assert r.status_code == 200
    await dm(app_client, org["id"], "Cleo", "again")
    await wait_for(lambda: rows(Turn, Turn.agent_id == cli["id"], Turn.status == "failed"),
                   timeout=60, msg="failed turn")
    failed = (await rows(Turn, Turn.agent_id == cli["id"], Turn.status == "failed"))[0]
    assert "isn't installed" in failed.error

    # The failed turn's message waits again: fixed and retried, the agent answers it.
    await app_client.patch(f"/api/v1/agents/{cli['id']}", json={
        "runner": {"preset": "custom", "command": str(script)}})
    await app_client.post(f"/api/v1/agents/{cli['id']}/retry")
    await wait_for(lambda: _completed(cli["id"], 2), timeout=60, msg="retried turn done")
    assert "ECHO: again" in (await _replies(cli["id"]))[-1]


async def test_the_bridge_gives_external_agents_pantheons_tools(app_client, monkeypatch):
    from app import mcpbridge
    from app.agents import external
    from app.main import app

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", kind="cli",
                           runner={"preset": "custom", "command": "x"})
    await make_agent(app_client, org["id"], "Ben")
    external._grants["tok"] = external.Grant(ada["id"], "trn_x", 0, {"send_message", "list_tasks"})
    try:
        assert (await app_client.get("/api/v1/bridge/tools", headers={"Authorization": "Bearer bad"})).status_code == 401
        listed = (await app_client.get("/api/v1/bridge/tools", headers={"Authorization": "Bearer tok"})).json()
        names = {t["name"] for t in listed}
        assert names == {"send_message", "list_tasks"}  # only what the turn was granted
        r = await app_client.post("/api/v1/bridge/call", headers={"Authorization": "Bearer tok"},
                                  json={"name": "write_file", "arguments": {}})
        assert r.json()["ok"] is False

        # Through the MCP server the CLI starts: list and call, relayed to the engine.
        real = httpx.AsyncClient
        monkeypatch.setattr(mcpbridge, "HEADERS", {"Authorization": "Bearer tok"})
        monkeypatch.setattr(mcpbridge, "URL", "http://t")
        monkeypatch.setattr(mcpbridge.httpx, "AsyncClient",
                            lambda **kw: real(transport=httpx.ASGITransport(app=app), **kw))
        tools = await mcpbridge._list_tools(None, None)
        assert {t.name for t in tools.tools} == {"send_message", "list_tasks"}
        from mcp import types

        res = await mcpbridge._call_tool(None, types.CallToolRequestParams(
            name="send_message", arguments={"to": "Ben", "content": "hello from the CLI"}))
        assert not res.is_error, res.content
        sent = await rows(Message, Message.sender_id == ada["id"])
        assert sent[-1].content == "hello from the CLI" and sent[-1].turn_id == "trn_x"
    finally:
        external._grants.pop("tok", None)


async def test_zeus_argus_and_kronos_can_run_on_any_agent(app_client, tmp_path):
    from app.agents import external
    from app.core.db import SessionLocal
    from app.models import Org
    from app.services import supervisor

    script = tmp_path / "echo_agent.py"
    script.write_text(ECHO, encoding="utf-8")
    zeus = await supervisor.ensure_meta_agent("zeus")
    r = await app_client.patch(f"/api/v1/agents/{zeus.id}", json={
        "kind": "cli", "runner": {"preset": "custom", "command": str(script)}})
    assert r.status_code == 200 and r.json()["kind"] == "cli" and r.json()["metaRole"] == "zeus"

    # He answers in his chat, with his rules and the bridge…
    await dm(app_client, supervisor.SYSTEM_ORG_ID, "Zeus", "build me a legal desk")
    await wait_for(lambda: _completed(zeus.id), timeout=60, msg="zeus cli turn")
    reply = (await _replies(zeus.id))[-1]
    assert "ECHO: build me a legal desk" in reply and "SYSTEM-HAS-RULES" in reply and "BRIDGE" in reply

    # …and the bridge carries his admin tools, as it does Argus's and Kronos's.
    async with SessionLocal() as session:
        system = await session.get(Org, supervisor.SYSTEM_ORG_ID)
        row = await session.get(Agent, zeus.id)
    assert {"create_org", "create_agent", "ask_kronos", "run_flow"} <= set(
        await external.bridge_tools(system, row))
    for role, tool in (("argus", "org_health"), ("kronos", "host_run")):
        meta = await supervisor.ensure_meta_agent(role)
        assert tool in await external.bridge_tools(system, meta)

    # Reset puts him back on Pantheon's own loop.
    back = await supervisor.reset_meta_agent("zeus")
    assert back.kind == "llm" and back.runner == {}


def _fake_remote(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    body = json.loads(request.content or b"{}")
    if url == "https://hook.test/agent":
        return httpx.Response(200, json={"reply": f"hooked: {body['message'].splitlines()[-1]}"})
    if url == "https://llm.test/v1/chat/completions":
        assert request.headers["authorization"] == "Bearer sk-test"
        n = len(body["messages"])
        chunks = [f'data: {json.dumps({"choices": [{"delta": {"content": c}}]})}\n\n'
                  for c in ("Hello ", f"({n} messages)")]
        return httpx.Response(200, text="".join(chunks) + "data: [DONE]\n\n",
                              headers={"content-type": "text/event-stream"})
    if url == "https://a2a.test/":
        assert body["method"] == "message/send"
        ctx = body["params"]["message"].get("contextId")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
            "kind": "task", "id": "t1", "contextId": "ctx-1", "status": {"state": "completed"},
            "artifacts": [{"parts": [{"kind": "text", "text": f"a2a done (context {ctx})"}]}]}})
    return httpx.Response(404)


async def test_remote_agents_over_webhooks_openai_and_a2a(app_client, monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(_plugin().httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(_fake_remote), **kw))
    org = await make_org(app_client)
    hook = await make_agent(app_client, org["id"], "Hook", kind="remote", runner={
        "preset": "webhook", "protocol": "webhook", "url": "https://hook.test/agent"})
    await dm(app_client, org["id"], "Hook", "ping")
    await wait_for(lambda: _completed(hook["id"]), timeout=30, msg="webhook turn")
    assert (await _replies(hook["id"]))[-1] == "hooked: ping"

    # OpenAI-compatible, with its key an org secret granted to it; the conversation is kept.
    gpt = await make_agent(app_client, org["id"], "Grokky", kind="remote", runner={
        "preset": "openai", "protocol": "openai", "url": "https://llm.test/v1", "secret": "LLM_KEY"})
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/secrets", json={
        "name": "LLM_KEY", "value": "sk-test", "domains": ["llm.test"], "agents": ["Grokky"]})
    assert r.status_code in (200, 201), r.text
    await dm(app_client, org["id"], "Grokky", "one")
    await wait_for(lambda: _completed(gpt["id"]), timeout=30, msg="openai turn 1")
    await dm(app_client, org["id"], "Grokky", "two")
    await wait_for(lambda: _completed(gpt["id"], 2), timeout=30, msg="openai turn 2")
    replies = await _replies(gpt["id"])
    assert replies[-2] == "Hello (2 messages)" and replies[-1] == "Hello (4 messages)"
    assert "history" not in (await app_client.get(f"/api/v1/agents/{gpt['id']}")).json()["runner"]

    a2a = await make_agent(app_client, org["id"], "Ava", kind="remote", runner={
        "preset": "a2a", "protocol": "a2a", "url": "https://a2a.test/"})
    await dm(app_client, org["id"], "Ava", "first")
    await wait_for(lambda: _completed(a2a["id"]), timeout=30, msg="a2a turn 1")
    await dm(app_client, org["id"], "Ava", "second")
    await wait_for(lambda: _completed(a2a["id"], 2), timeout=30, msg="a2a turn 2")
    assert (await _replies(a2a["id"]))[-2:] == ["a2a done (context None)", "a2a done (context ctx-1)"]


async def test_a_person_answers_through_the_interface(app_client):
    org = await make_org(app_client)
    hana = await make_agent(app_client, org["id"], "Hana", kind="human")
    await dm(app_client, org["id"], "Hana", "Can you approve the budget?")
    waiting = (await app_client.get(f"/api/v1/agents/{hana['id']}/human")).json()["waiting"]
    assert [m["content"] for m in waiting] == ["Can you approve the budget?"]
    items = await rows(UserInboxItem, UserInboxItem.kind == "human")
    assert items and "Hana" in items[0].title and not items[0].read
    assert await rows(Turn, Turn.agent_id == hana["id"]) == []  # never takes a turn itself

    r = await app_client.post(f"/api/v1/agents/{hana['id']}/human/answer",
                              json={"content": "Approved, up to 5k."})
    assert r.status_code == 200, r.text
    assert (await _replies(hana["id"]))[-1] == "Approved, up to 5k."
    assert (await app_client.get(f"/api/v1/agents/{hana['id']}/human")).json()["waiting"] == []
    r = await app_client.post(f"/api/v1/agents/{hana['id']}/human/answer", json={"content": "x"})
    assert r.status_code == 400

    # A person as a flow step: their answer is the step's result.
    flow = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "Sign-off", "graph": {
        "nodes": [{"id": "ok", "title": "Sign off", "agent": "Hana"}], "edges": []}})).json()
    run = (await app_client.post(f"/api/v1/flows/{flow['id']}/runs", json={"input": "launch?"})).json()
    await app_client.post(f"/api/v1/agents/{hana['id']}/human/answer", json={"content": "Go."})
    done = (await app_client.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "done" and done["steps"]["ok"]["output"] == "Go."
    agent = (await rows(Agent, Agent.id == hana["id"]))[0]
    assert agent.runtime_status == "idle"
    _ = Path
