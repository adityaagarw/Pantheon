"""Kronos: Zeus asks it for work, and it acts on the host through the runner bridge."""

from __future__ import annotations

import asyncio

import pytest

from app.models import Message
from app.services import host as host_svc
from app.services.supervisor import SYSTEM_ORG_ID
from app.tools.base import ToolError
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, rows, say, script, tool_results


async def _fake_runner(client, headers, served: list[dict]) -> None:
    while True:
        r = await client.get("/api/v1/host/next?wait=2", headers=headers)
        if r.status_code == 204:
            continue
        job = r.json()
        served.append(job)
        await client.post("/api/v1/host/result", headers=headers, json={
            "id": job["id"], "ok": True, "result": "    1\tprint('hello from the host')"})


async def test_zeus_asks_kronos_who_works_through_the_runner(app_client):
    zeus = (await app_client.get("/api/v1/meta/zeus")).json()["agent"]
    kronos = (await app_client.get("/api/v1/meta/kronos")).json()["agent"]
    tools = {t["name"]: t.get("approval") for t in kronos["tools"]}
    assert {"host_read", "code_start", "code_apply"} <= set(tools)
    assert tools["host_run"] == "ask"
    assert "ask_kronos" in {t["name"] for t in zeus["tools"]}

    headers = {"X-Host-Token": host_svc.token()}
    bad = await app_client.post("/api/v1/host/hello", json={}, headers={"X-Host-Token": "x"})
    assert bad.status_code == 401
    await app_client.post("/api/v1/host/hello", json={"repo": "/srv/pantheon"}, headers=headers)
    status = (await app_client.get("/api/v1/host/status")).json()
    assert status["connected"] and status["info"]["repo"] == "/srv/pantheon"

    served: list[dict] = []
    runner = asyncio.create_task(_fake_runner(app_client, headers, served))
    try:
        script(zeus["id"], call("ask_kronos", request="Add a built-in hello tool"),
               say("Asked Kronos."), then=say("ok"))
        kronos_prompts = script(
            kronos["id"], call("host_read", path="server/app/main.py"),
            call("message_agent", org="Pantheon", agent="Zeus", content="Done: hello tool added"),
            say("done"), then=say("ok"))
        await dm(app_client, SYSTEM_ORG_ID, "Zeus", "we need a hello tool")

        replies = await wait_for(lambda: rows(Message, Message.kind == "supervisor"),
                                 timeout=30, msg="Kronos replied to Zeus")
        assert "hello tool added" in replies[0].content
        assert "Add a built-in hello tool" in last_human(kronos_prompts[0])
        assert "hello from the host" in tool_results(kronos_prompts[1])[0]
        assert served[0]["op"] == "read" and served[0]["args"]["path"] == "server/app/main.py"
    finally:
        runner.cancel()


async def test_tools_say_when_the_runner_is_away():
    host_svc.bridge.last_seen = 0
    with pytest.raises(ToolError, match="Pantheon Host isn't running"):
        await host_svc.bridge.call("info", {})
