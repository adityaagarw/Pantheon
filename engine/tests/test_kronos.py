"""Kronos: Zeus asks it for work, and it acts on this computer (Pantheon Host, built in)."""

from __future__ import annotations

import json

import pytest

from app import host
from app.models import Message
from app.services.host import bridge
from app.services.supervisor import SYSTEM_ORG_ID
from app.tools.base import ToolError
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, rows, say, script, tool_results


@pytest.fixture
async def allowed(tmp_path, app_client):
    folder = tmp_path / "projects"
    folder.mkdir()
    (folder / "notes.txt").write_text("print('hello from this computer')\n", encoding="utf-8")
    r = await app_client.post("/api/v1/host/folders", json={"path": str(folder)})
    assert r.status_code == 200, r.text
    yield folder
    await host.set_folders([])


async def test_zeus_asks_kronos_who_works_on_this_computer(app_client, allowed):
    zeus = (await app_client.get("/api/v1/meta/zeus")).json()["agent"]
    kronos = (await app_client.get("/api/v1/meta/kronos")).json()["agent"]
    tools = {t["name"]: t.get("approval") for t in kronos["tools"]}
    assert {"host_read", "code_start", "code_apply"} <= set(tools)
    assert tools["host_run"] == "ask"
    assert "ask_kronos" in {t["name"] for t in zeus["tools"]}

    status = (await app_client.get("/api/v1/host/status")).json()
    assert status["connected"] and str(allowed.resolve()) in status["info"]["allowed"]

    script(zeus["id"], call("ask_kronos", request="Tidy my notes"),
           say("Asked Kronos."), then=say("ok"))
    kronos_prompts = script(
        kronos["id"], call("host_read", path=str(allowed / "notes.txt")),
        call("message_agent", org="Pantheon", agent="Zeus", content="Done: notes tidied"),
        say("done"), then=say("ok"))
    await dm(app_client, SYSTEM_ORG_ID, "Zeus", "tidy my notes please")

    replies = await wait_for(lambda: rows(Message, Message.kind == "supervisor"),
                             timeout=30, msg="Kronos replied to Zeus")
    assert "notes tidied" in replies[0].content
    assert "Tidy my notes" in last_human(kronos_prompts[0])
    assert "hello from this computer" in tool_results(kronos_prompts[1])[0]


async def test_only_allowed_folders_and_the_source(tmp_path, allowed, app_client):
    elsewhere = tmp_path / "private.txt"
    elsewhere.write_text("secret plans", encoding="utf-8")
    with pytest.raises(ToolError, match="outside the folders"):
        await bridge.call("read", {"path": str(elsewhere)})
    await bridge.call("write", {"path": str(allowed / "new.txt"), "content": "hi"})
    assert (allowed / "new.txt").read_text(encoding="utf-8") == "hi"
    info = json.loads(await bridge.call("info", {}))
    assert info["repo"] == (str(host.REPO) if host.REPO else None)

    bad = await app_client.post("/api/v1/host/folders", json={"path": str(tmp_path / "nope")})
    assert bad.status_code == 400
    await app_client.post("/api/v1/host/folders/remove", json={"path": str(allowed)})
    with pytest.raises(ToolError, match="outside the folders"):
        await bridge.call("read", {"path": str(allowed / "notes.txt")})
