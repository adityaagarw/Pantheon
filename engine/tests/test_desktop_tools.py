"""desktop_* tools: agents drive this computer through cua-driver (faked here)."""

from __future__ import annotations

import pytest

from app import computer as computers
from app.computer import driver as driver_mod
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script, tool_results

PNG = "iVBORw0KGgo="


@pytest.fixture
def fake_driver(monkeypatch):
    calls: list[tuple[str, dict]] = []

    async def fake_call(tool, args):
        calls.append((tool, args))
        if tool == "get_window_state":
            return "## Notepad [1] Edit", [f"data:image/png;base64,{PNG}"], False
        if tool == "click" and args.get("element_token") == "s0000002a:9":
            return "stale element token", [], True
        return "ok", [], False

    monkeypatch.setattr(driver_mod.driver, "call", fake_call)
    return calls


async def _done(agent_id, n):
    from app.models import Agent, ToolCall

    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    return len(calls) >= n and (await rows(Agent, Agent.id == agent_id))[0].runtime_status == "idle"


async def test_off_until_this_computer_is_chosen(app_client, fake_driver):
    from app.models import Agent, Org
    from app.tools.resolve import resolve_tools

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[{"name": "desktop_windows"}])
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == ada["id"]))[0]
    assert "desktop_windows" not in (await resolve_tools(o, a)).tools  # no computer chosen
    await computers.set_backend("driver")
    try:
        assert "desktop_windows" in (await resolve_tools(o, a)).tools
    finally:
        await computers.set_backend("off")
    assert fake_driver == []


async def test_driving_apps_in_the_background(app_client, fake_driver):
    await computers.set_backend("driver")
    try:
        org = await make_org(app_client)
        tools = [{"name": n} for n in ("desktop_look", "desktop_click", "desktop_act")]
        ada = await make_agent(app_client, org["id"], "Ada", tools=tools, model={"vision": True})
        ap = script(ada["id"], call("desktop_look", pid=42, window_id=7),
                    call("desktop_click", pid=42, element_token="s0000002a:9"),
                    call("desktop_act", pid=42, steps=[{"tool": "type_text", "args": {"text": "hi"}}]),
                    say("done"), then=say("ok"))
        await dm(app_client, org["id"], "Ada", "Write hi in Notepad.")
        await wait_for(lambda: _done(ada["id"], 3), msg="worked")
        assert [c[0] for c in fake_driver] == ["get_window_state", "click", "run_steps"], fake_driver
        assert {c[1]["session"] for c in fake_driver} == {f"pantheon-{ada['id']}"}  # its own cursor
        results = tool_results(ap[3])
        assert "Notepad" in results[0] and "stale element token" in results[1]
        assert (await app_client.get("/api/v1/computer")).json()["active"]["agent"] == "Ada"

        await app_client.post("/api/v1/computer/stop")
        bp = script(ada["id"], call("desktop_look", pid=42, window_id=7), say("stopped"), then=say("ok"))
        await dm(app_client, org["id"], "Ada", "Look again.")
        await wait_for(lambda: _done(ada["id"], 4), msg="refused")
        assert "paused" in tool_results(bp[1])[-1] and len(fake_driver) == 3
    finally:
        await computers.set_backend("off")


async def test_schemas_come_from_the_driver():
    from app.tools.base import builtin

    look = builtin("desktop_look")
    assert look.category == "computer" and "session" not in look.parameters["properties"]
    assert {"pid", "window_id"} <= set(look.parameters["properties"])
    assert builtin("desktop_act").parameters["properties"]["steps"]["type"] == "array"


async def test_the_sandbox_desktop_is_relayed_only_once_set_up(app_client):
    """The desktop page comes through Pantheon (which signs in); none before setup, and the
    status never hands out the desktop's password."""
    from app.computer import sandbox

    sandbox._state.clear()
    sandbox._state["loaded"] = True  # nothing set up (and don't read a stale row)
    try:
        assert (await app_client.get("/api/v1/computer/sandbox/desktop/index.html")).status_code == 404
        sandbox._state.update(vnc_port=1, vnc_password="secret")  # nothing listens on port 1
        assert (await app_client.get("/api/v1/computer/sandbox/desktop/index.html")).status_code == 502
        assert "secret" not in (await app_client.get("/api/v1/computer/sandbox")).text
    finally:
        sandbox._state.clear()
