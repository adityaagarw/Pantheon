"""Cron schedules, the Stage, images in tool results, computer use, the browser."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.models import Agent, Schedule, ToolCall
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script

TINY_JPEG = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQ=="


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


def _out(prompts, i: int) -> str:
    outs = [str(m.content) for m in prompts[i] if isinstance(m, ToolMessage)]
    return outs[-1] if outs else ""


# --- cron ------------------------------------------------------------------------------


async def test_agents_schedule_reminders_and_cron_jobs(app_client):
    from app.core.db import SessionLocal
    from app.services import schedules

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    ap = script(ada["id"],
                call("schedule", message="check the build", cron="*/1 * * * *"),
                call("schedule", message="check the build", cron="0 9 * * 1-5",
                     timezone="Asia/Kolkata"),
                call("schedule", message="call Ben back", in_minutes=30),
                call("list_schedules"), say("Scheduled."))
    await dm(app_client, org["id"], "Ada", "Remind yourself to check the build every weekday.")
    await wait_for(lambda: _done(ada["id"], 4), msg="scheduled")
    assert "at most every 5 minutes" in _out(ap, 1)
    assert "'0 9 * * 1-5' (Asia/Kolkata)" in _out(ap, 2)
    assert "once" in _out(ap, 3)
    listing = _out(ap, 4)
    assert "0 9 * * 1-5" in listing and "call Ben back" in listing

    rows_ = await rows(Schedule, Schedule.agent_id == ada["id"])
    weekday = next(s for s in rows_ if s.cron)
    local = weekday.next_run_at.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
    assert (local.hour, local.minute) == (9, 0) and local.weekday() < 5

    # Make both due; the one-off fires once and is done, the cron job moves on.
    async with SessionLocal() as s:
        for row in (await s.execute(__import__("sqlalchemy").select(Schedule))).scalars():
            row.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        await s.commit()
    later = script(ada["id"], say("On it."), then=say("ok"))
    assert await schedules.tick() == 2
    await wait_for(lambda: len(later) >= 1, msg="reminder delivered")
    assert "Reminder you scheduled" in last_human(later[0])
    after = {s.id: s for s in await rows(Schedule, Schedule.agent_id == ada["id"])}
    assert after[weekday.id].enabled and after[weekday.id].next_run_at > datetime.now(UTC)
    once = next(s for s in after.values() if not s.cron)
    assert not once.enabled and once.runs == 1
    api = (await app_client.get(f"/api/v1/agents/{ada['id']}/schedules")).json()
    assert [s["cron"] for s in api] == ["0 9 * * 1-5"]


# --- the Stage ---------------------------------------------------------------------------


async def test_stage_pages_are_served_sandboxed_with_the_library(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_docs", "stage_show", "stage_check", "stage_list")])
    code = 'const s = new Stage({ title: "Hi" }); s.caption("</script> is escaped");'
    ap = script(ada["id"], call("stage_docs"), call("stage_show", title="Hello", code=code),
                call("stage_show", title="Hello", code=code + " s.wait(1);"), say("Look!"))
    await dm(app_client, org["id"], "Ada", "Show me something.")
    await wait_for(lambda: _done(ada["id"], 3), msg="shown")
    assert "manim-style" in _out(ap, 1)
    assert "version 1" in _out(ap, 2) and "version 2" in _out(ap, 3)

    pages = (await app_client.get(f"/api/v1/orgs/{org['id']}/stage")).json()
    assert len(pages) == 1 and pages[0]["version"] == 2
    view = await app_client.get(f"/api/v1/stage/{pages[0]['id']}/view")
    assert view.status_code == 200
    assert "sandbox allow-scripts" in view.headers["content-security-policy"]
    body = view.text
    assert '"stage": "/api/v1/stage-lib/stage.js"' in body
    assert "<\\/script> is escaped" in body and "</script> is escaped" not in body
    lib = await app_client.get("/api/v1/stage-lib/stage.js")
    assert lib.status_code == 200 and lib.headers["access-control-allow-origin"] == "*"
    assert "export class Stage" in lib.text
    three = await app_client.get("/api/v1/stage-lib/three/three.module.js")
    assert three.status_code == 200 and three.headers["content-type"].startswith("text/javascript")
    assert (await app_client.get("/api/v1/stage-lib/../services/stage.py")).status_code == 404


# --- images in tool results -----------------------------------------------------------------


@pytest.fixture
def camera_tool():
    from app.tools import base
    from app.tools.base import ToolOutput

    n = {"i": 0}

    async def snap(args, ctx):
        n["i"] += 1
        return ToolOutput(f"shot {n['i']}", [TINY_JPEG])

    base.register(base.ToolSpec("test_camera", "Take a picture.", {"type": "object",
                                "properties": {}}, snap))
    yield
    base.unregister("test_camera")


async def test_screenshots_reach_vision_models_and_old_ones_are_pruned(app_client, camera_tool):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[{"name": "test_camera"}],
                           model={"vision": True})
    ap = script(ada["id"], call("test_camera"), call("test_camera"), call("test_camera"),
                say("seen"))
    await dm(app_client, org["id"], "Ada", "Look three times.")
    await wait_for(lambda: _done(ada["id"], 3), msg="looked")

    def images(prompt):
        return [m for m in prompt if isinstance(m, HumanMessage) and isinstance(m.content, list)
                and any(b.get("type") == "image_url" for b in m.content)]

    assert len(images(ap[1])) == 1
    # The screenshot comes right after its tool result.
    i = next(k for k, m in enumerate(ap[1]) if isinstance(m, ToolMessage))
    assert ap[1][i + 1] is images(ap[1])[0]
    assert len(images(ap[3])) == 2  # the oldest one is replaced by a placeholder
    assert any("earlier screenshot" in str(m.content) for m in ap[3])
    stored = await rows(ToolCall, ToolCall.agent_id == ada["id"])
    assert all("base64" not in (t.result or "") for t in stored)


async def test_text_only_models_are_told_instead(app_client, camera_tool):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[{"name": "test_camera"}])
    ap = script(ada["id"], call("test_camera"), say("ok"))
    await dm(app_client, org["id"], "Ada", "Look.")
    await wait_for(lambda: _done(ada["id"], 1), msg="looked")
    assert "isn't set up to see images" in _out(ap, 1)
    assert not any(isinstance(m, HumanMessage) and isinstance(m.content, list) for m in ap[1])


# --- computer use ----------------------------------------------------------------------------


async def test_computer_use_drives_cua_and_one_agent_at_a_time(app_client, monkeypatch):
    from app.computer import cua

    sent: list[tuple[str, dict]] = []

    async def fake_cmd(self, command, timeout=60, **params):
        sent.append((command, params))
        if command == "screenshot":
            return {"success": True, "image_data": "AAAA", "format": "jpeg"}
        return {"success": True}

    monkeypatch.setattr(cua.CuaComputer, "cmd", fake_cmd)
    org = await make_org(app_client)
    tools = [{"name": n} for n in ("computer_click", "computer_type", "computer_key")]
    ada = await make_agent(app_client, org["id"], "Ada", tools=tools, model={"vision": True})
    ben = await make_agent(app_client, org["id"], "Ben", tools=tools)
    ap = script(ada["id"], call("computer_click", x=100, y=200, double=True),
                call("computer_type", text="hello"), call("computer_key", keys="ctrl+l"),
                say("done"))
    await dm(app_client, org["id"], "Ada", "Use the computer.")
    await wait_for(lambda: _done(ada["id"], 3), msg="used")
    assert ("double_click", {"x": 100, "y": 200}) in sent
    assert ("type_text", {"text": "hello"}) in sent
    assert ("hotkey", {"keys": ["ctrl", "l"]}) in sent
    assert sent[-1][0] == "screenshot"
    assert "Double-clicked at (100, 200)" in _out(ap, 1)

    bp = script(ben["id"], call("computer_key", keys="Return"), say("waiting"))
    await dm(app_client, org["id"], "Ben", "Press enter on the computer.")
    await wait_for(lambda: _done(ben["id"], 1), msg="ben tried")
    assert "in use by Ada" in _out(bp, 1)


async def test_computer_shell_reaches_the_real_wrapper(app_client, monkeypatch):
    """computer_shell goes through the real CuaComputer.cmd (no fake): its `command`
    argument used to collide with cmd()'s own first parameter (Zeus-triaged bug)."""
    import json

    import httpx

    from app.tools import computer as computer_tools

    computer_tools._lease.clear()  # another test's agent may still hold the desktop
    seen: list[dict] = []

    def server(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        result = {"success": True, "stdout": "hello\n", "stderr": "", "return_code": 0}
        return httpx.Response(200, text="data: " + json.dumps(result) + "\n\n")

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, transport=httpx.MockTransport(server), **k))
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada",
                           tools=[{"name": "computer_shell", "approval": "auto"}])
    ap = script(ada["id"], call("computer_shell", command="echo hello"), say("done"))
    await dm(app_client, org["id"], "Ada", "Say hello on the computer.")
    await wait_for(lambda: _done(ada["id"], 1), msg="ran")
    assert seen == [{"command": "run_command", "params": {"command": "echo hello"}}], _out(ap, 1)
    assert _out(ap, 1) == "exit 0\nhello\n"


# --- the browser -----------------------------------------------------------------------------


def test_browser_refuses_private_and_internal_addresses():
    from app.browser.manager import allowed

    assert not allowed("http://127.0.0.1:8710/api/v1/approvals")
    assert not allowed("http://localhost:3710/")
    assert not allowed("http://backend:8710/")
    assert not allowed("http://192.168.1.10:8000/v1/models")
    assert not allowed("file:///etc/passwd")
    assert allowed("http://127.0.0.1:8710/api/v1/stage/stg_x/view")  # Stage checks
    assert allowed("data:text/html,hi")


async def test_browser_snapshot_numbers_interactive_elements():
    from app.browser.manager import manager

    s = await manager.session("test-agent")
    try:
        await s.page.set_content(
            "<h1>Shop</h1><a href='/x'>Offers</a><button>Add to cart</button>"
            "<input placeholder='Search'><div style='display:none'><button>Hidden</button></div>")
        data = await manager.snapshot(s)
        assert data["items"] == ["[1] a: Offers", "[2] button: Add to cart", "[3] text: Search"]
        assert "Shop" in data["text"] and s.shot[:2] == b"\xff\xd8"
    finally:
        await manager.stop()
