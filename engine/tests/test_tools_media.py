"""Cron schedules, the Stage, images in tool results, computer use, the browser."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.core.config import settings
from app.models import Agent, Org, Schedule, ToolCall
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


async def test_a_stage_page_can_come_from_a_workspace_file(app_client, workspace_root):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_show", "write_file")])
    code = 'const s = new Stage({ title: "From a file" });'
    ap = script(ada["id"], call("write_file", path="lessons/ch01.js", content=code),
                call("stage_show", title="Ch 1", file="lessons/ch01.js"),
                call("stage_show", title="Ch 2", file="lessons/missing.js"), say("ok"))
    await dm(app_client, org["id"], "Ada", "Show chapter 1.")
    await wait_for(lambda: _done(ada["id"], 3), msg="shown")
    assert "version 1" in _out(ap, 2) and "no file 'lessons/missing.js'" in _out(ap, 3)
    pages = (await app_client.get(f"/api/v1/orgs/{org['id']}/stage")).json()
    assert [p["title"] for p in pages] == ["Ch 1"]
    view = await app_client.get(f"/api/v1/stage/{pages[0]['id']}/view")
    assert "From a file" in view.text


async def test_stage_pages_can_include_a_shared_kit(app_client, workspace_root):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_show", "write_file")])
    kit = 'globalThis.glow = () => "KIT-GLOW"; // </script> stays inside'
    ap = script(ada["id"], call("write_file", path="lessons/kit.js", content=kit),
                call("write_file", path="lessons/ch01.js", content="glow(); // PAGE-CODE"),
                call("write_file", path="lessons/cover.html",
                     content="<html><body><h1>COVER</h1></body></html>"),
                call("stage_show", title="Ch 1", file="lessons/ch01.js",
                     include=["lessons/kit.js"]),
                call("stage_show", title="Cover", file="lessons/cover.html",
                     include=["lessons/kit.js"]), say("ok"))
    await dm(app_client, org["id"], "Ada", "Show them.")
    await wait_for(lambda: _done(ada["id"], 5), msg="shown")
    assert "version 1" in _out(ap, 4) and "version 1" in _out(ap, 5)
    pages = {p["title"]: p for p in (await app_client.get(
        f"/api/v1/orgs/{org['id']}/stage")).json()}
    lesson = (await app_client.get(f"/api/v1/stage/{pages['Ch 1']['id']}/view")).text
    assert lesson.index("KIT-GLOW") < lesson.index("PAGE-CODE")
    cover = (await app_client.get(f"/api/v1/stage/{pages['Cover']['id']}/view")).text
    assert cover.index("KIT-GLOW") < cover.index("COVER") and "<\\/script> stays" in cover


async def test_archived_stage_pages_are_kept_but_out_of_the_way(app_client, workspace_root):
    from app.services import stage

    org = await make_org(app_client)
    old = await stage.save(org["id"], None, "Ch 1 · Start", code="// OLD")
    before = stage.to_dict(old)["updatedAt"]
    moved = (await app_client.patch(f"/api/v1/stage/{old.id}", json={"folder": "archive"})).json()
    assert moved["folder"] == "archive" and moved["updatedAt"] == before  # not an edit
    assert "OLD" in (await app_client.get(f"/api/v1/stage/{old.id}/view")).text
    new = await stage.save(org["id"], None, "Ch 1 · Start", code="// NEW")
    assert new.id != old.id and (await stage.get(old.id)).source == "// OLD"
    assert (await app_client.patch(f"/api/v1/stage/{old.id}", json={"folder": "x"})).status_code == 400
    back = (await app_client.patch(f"/api/v1/stage/{old.id}", json={"folder": ""})).json()
    assert back["folder"] == ""


async def test_agents_archive_their_stray_stage_pages(app_client, workspace_root):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_show", "stage_archive", "stage_list")])
    ap = script(ada["id"], call("stage_show", title="Ch 4 · Feedback", code="// REAL"),
                call("stage_show", title="Ch 4 probe", code="// PROBE"),
                call("stage_archive", pages=["Ch 4"]),
                call("stage_archive", pages=["ch 4 PROBE", "nope"]),
                call("stage_list"),
                call("stage_archive", pages=["Ch 4 probe"], restore=True), say("ok"))
    await dm(app_client, org["id"], "Ada", "Tidy up.")
    await wait_for(lambda: _done(ada["id"], 6), msg="archived")
    assert "ERROR" in _out(ap, 3)  # a title start never archives the real chapter
    assert "Archived 1" in _out(ap, 4) and "'nope'" in _out(ap, 4)
    assert "Ch 4 probe" not in _out(ap, 5) and "Ch 4 · Feedback" in _out(ap, 5)
    assert "Restored 1" in _out(ap, 6)
    pages = (await app_client.get(f"/api/v1/orgs/{org['id']}/stage")).json()
    assert all(p["folder"] == "" for p in pages)


async def test_stage_pages_package_as_one_site(app_client, workspace_root, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_show", "stage_package")])
    ap = script(ada["id"], call("stage_show", title="Contents", code="// CONTENTS"),
                call("stage_show", title="Ch 1 · Start", code="// CH-ONE"),
                call("stage_package", name="My Book", pages=["Contents", "Ch 1"]), say("ok"))
    await dm(app_client, org["id"], "Ada", "Package it.")
    await wait_for(lambda: _done(ada["id"], 3), msg="packaged")
    out = _out(ap, 3)
    assert f"/api/v1/sites/{org['id']}/my-book/" in out and "ch-1-start.html" in out
    base = f"/api/v1/sites/{org['id']}/my-book"
    assert (await app_client.get(base)).status_code in (301, 302, 307)
    home = await app_client.get(base + "/")
    assert "CONTENTS" in home.text and "sandbox" in home.headers["content-security-policy"]
    assert '<script src="site.js">' in home.text and '"stage": "./lib/stage.js"' in home.text
    listing = (await app_client.get(base + "/site.js")).text
    assert '"file": "index.html"' in listing and '"file": "ch-1-start.html"' in listing
    assert "CH-ONE" in (await app_client.get(base + "/ch-1-start.html")).text
    lib = await app_client.get(base + "/lib/stage.js")
    assert lib.status_code == 200 and lib.headers["access-control-allow-origin"] == "*"
    assert (await app_client.get(base + ".zip")).headers["content-type"] == "application/zip"
    assert (await app_client.get(base + "/%2e%2e/%2e%2e/%2e%2e/host.token")).status_code == 404


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


@pytest.fixture
async def sandbox_computer(app_client):
    """The sandbox desktop (the cua-desktop pack) chosen in Settings → This computer."""
    from app import computer as computers

    await computers.set_backend("cua")
    yield
    await computers.set_backend("off")


async def test_computer_use_is_off_until_chosen_and_can_be_stopped(app_client):
    from app import computer as computers

    st = (await app_client.get("/api/v1/computer")).json()
    assert st["backend"] == "off" and {"native", "cua"} <= {b["id"] for b in st["backends"]}
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada",
                           tools=[{"name": "computer_key", "approval": "auto"}])
    ap = script(ada["id"], call("computer_key", keys="Return"), say("off"), then=say("ok"))
    await dm(app_client, org["id"], "Ada", "Press enter.")
    await wait_for(lambda: _done(ada["id"], 1), msg="tried")
    assert "not available to you" in _out(ap, 1)  # no computer chosen: no computer tools

    assert (await app_client.put("/api/v1/computer", json={"backend": "cua"})).status_code == 200
    stopped = (await app_client.post("/api/v1/computer/stop")).json()
    assert stopped["paused"]
    try:
        with pytest.raises(computers.ComputerOff, match="paused"):
            computers.current()
        assert (await app_client.post("/api/v1/computer/resume")).json()["paused"] is None
        assert computers.current() is not None
        bad = await app_client.put("/api/v1/computer", json={"backend": "mainframe"})
        assert bad.status_code == 400
    finally:
        await computers.set_backend("off")


class FakeDriver:
    """The sandbox's cua-driver (app/computer/sandbox.py), recorded."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call(self, tool, args):
        self.calls.append((tool, {k: v for k, v in args.items() if k != "session"}))
        if tool == "get_desktop_state":
            return "desktop screenshot", ["data:image/png;base64,AAAA"], False
        return "ok", [], False


@pytest.fixture
def fake_sandbox(monkeypatch):
    from app.computer import sandbox

    fake = FakeDriver()

    async def driver():
        return fake

    monkeypatch.setattr(sandbox, "driver", driver)
    return fake


async def test_computer_use_drives_the_sandbox_and_one_agent_at_a_time(
        app_client, sandbox_computer, fake_sandbox):
    org = await make_org(app_client)
    tools = [{"name": n} for n in ("computer_click", "computer_type", "computer_key")]
    ada = await make_agent(app_client, org["id"], "Ada", tools=tools, model={"vision": True})
    ben = await make_agent(app_client, org["id"], "Ben", tools=tools)
    ap = script(ada["id"], call("computer_click", x=100, y=200, double=True),
                call("computer_type", text="hello"), call("computer_key", keys="ctrl+l"),
                say("done"))
    await dm(app_client, org["id"], "Ada", "Use the computer.")
    await wait_for(lambda: _done(ada["id"], 3), msg="used")
    desktop = {"kind": "desktop", "display_id": "primary"}
    calls = fake_sandbox.calls
    assert ("click", {"target": desktop, "x": 100, "y": 200, "button": "left", "count": 2}) in calls
    assert ("type_text", {"scope": "desktop", "text": "hello"}) in calls
    assert ("hotkey", {"scope": "desktop", "keys": ["ctrl", "l"]}) in calls
    assert calls[-1][0] == "get_desktop_state"  # every action returns the screen after it
    assert "Double-clicked at (100, 200)" in _out(ap, 1)

    bp = script(ben["id"], call("computer_key", keys="Return"), say("waiting"))
    await dm(app_client, org["id"], "Ben", "Press enter on the computer.")
    await wait_for(lambda: _done(ben["id"], 1), msg="ben tried")
    assert "in use by Ada" in _out(bp, 1)


async def test_computer_shell_runs_in_the_sandbox(app_client, monkeypatch, sandbox_computer):
    from app.computer import sandbox
    from app.tools import computer as computer_tools

    computer_tools._lease.clear()  # another test's agent may still hold the desktop
    seen: list[tuple] = []

    async def fake_docker(*args, timeout=60):
        seen.append(args)
        return 0, "hello"

    async def running():
        return None

    monkeypatch.setattr(sandbox, "_docker", fake_docker)
    monkeypatch.setattr(sandbox, "ensure_running", running)
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada",
                           tools=[{"name": "computer_shell", "approval": "auto"}])
    ap = script(ada["id"], call("computer_shell", command="echo hello"), say("done"))
    await dm(app_client, org["id"], "Ada", "Say hello on the computer.")
    await wait_for(lambda: _done(ada["id"], 1), msg="ran")
    assert seen == [("exec", "-u", "1000", "pantheon-sandbox", "bash", "-lc", "echo hello")], _out(ap, 1)
    assert _out(ap, 1) == "exit 0\nhello"


async def test_each_computer_offers_its_own_tools(app_client):
    from app import computer as computers
    from app.tools.resolve import resolve_tools

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": "computer_click"}, {"name": "desktop_look"}, {"name": "desktop_act"}])
    o, a = (await rows(Org, Org.id == org["id"]))[0], (await rows(Agent, Agent.id == ada["id"]))[0]
    offered = {}
    try:
        for backend in ("off", "native", "driver", "cua"):
            await computers.set_backend(backend)
            offered[backend] = {n for n in (await resolve_tools(o, a)).tools
                                if n.startswith(("computer_", "desktop_"))}
    finally:
        await computers.set_backend("off")
    assert offered["off"] == set()
    assert offered["native"] == {"computer_click"}
    assert offered["driver"] == {"desktop_look", "desktop_act"}
    assert offered["cua"] == {"computer_click", "desktop_look"}  # no batches in the sandbox


# --- the browser -----------------------------------------------------------------------------


def test_browser_refuses_private_and_internal_addresses():
    from app.browser.manager import allowed

    assert not allowed("http://127.0.0.1:8710/api/v1/approvals")
    assert not allowed("http://localhost:3710/")
    assert not allowed("http://backend:8710/")
    assert not allowed("http://192.168.1.10:8000/v1/models")
    assert not allowed("file:///etc/passwd")
    assert allowed(f"{settings.stage_base_url}/api/v1/stage/stg_x/view")  # Stage checks
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


async def test_stage_check_finds_a_page_by_title_or_workspace_file(app_client, workspace_root):
    from app.services import stage
    from app.tools import base
    from app.tools.base import ToolContext, ToolError, agent_workdir
    from app.tools.stagetools import find_page, stage_check

    assert base.builtin("stage_check").handler is stage_check  # the tool, not the helper

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == ada["id"]))[0]
    ctx = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    page = await stage.save(o.id, a.id, "my-bookings.html — Tula Yoga Loft", html="<p>v1</p>")
    assert (await find_page(ctx, "My-bookings.html: tula yoga loft")).id == page.id
    assert (await find_page(ctx, "Tula Yoga")).id == page.id  # part of the title
    assert (await find_page(ctx, page.id)).id == page.id
    # A workspace file: the page of that name is updated to the file as it is now.
    wd = agent_workdir(o, a)
    wd.mkdir(parents=True, exist_ok=True)
    (wd / "my-bookings.html").write_text("<p>v2</p>", encoding="utf-8")
    p = await find_page(ctx, "my-bookings.html")
    assert p.id == page.id and p.version == 2 and "v2" in p.source
    # ...and one with no page yet goes on the Stage.
    (wd / "studio.html").write_text("<p>studio</p>", encoding="utf-8")
    assert (await find_page(ctx, "studio.html")).title == "studio.html"
    with pytest.raises(ToolError, match="no stage page"):
        await find_page(ctx, "nothing-like-this")
    # The acceptance checker's test pages run but stay off the user's list, re-checked in place.
    (wd / ".acceptance").mkdir()
    (wd / ".acceptance" / "check.html").write_text("<p>c1</p>", encoding="utf-8")
    first = await find_page(ctx, ".acceptance/check.html")
    (wd / ".acceptance" / "check.html").write_text("<p>c2</p>", encoding="utf-8")
    again = await find_page(ctx, ".acceptance/check.html")
    assert first.folder == stage.ARCHIVE and again.id == first.id and "c2" in again.source
    assert all(p.folder == stage.ARCHIVE for p in await stage.for_org(o.id)
               if p.title.startswith(".acceptance/"))


async def test_stage_check_opens_a_workspace_page_as_a_site_and_follows_a_path(app_client, workspace_root):
    """Checked on the Stage, a workspace page lost its links, shared files and the pages it
    leads to, so agents started servers and wrote probe pages to follow a booking from one
    screen to the next. Now it opens from the workspace like a real site."""
    from app.browser.manager import manager
    from app.services import preview, stage
    from app.tools.base import ToolContext, agent_workdir
    from app.tools.stagetools import stage_check

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    o = (await rows(Org, Org.id == org["id"]))[0]
    a = (await rows(Agent, Agent.id == ada["id"]))[0]
    ctx = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    wd = agent_workdir(o, a)
    (wd / "css").mkdir(parents=True, exist_ok=True)
    (wd / "css" / "site.css").write_text("body { font: 16px sans-serif }", encoding="utf-8")
    (wd / "index.html").write_text(
        '<link rel="stylesheet" href="css/site.css"><a href="schedule.html">Schedule</a>'
        '<p style="color:#bbb;background:#fff">Mats left today</p>'
        '<div style="width:2000px" class="week">Mon Tue Wed</div>', encoding="utf-8")
    (wd / "schedule.html").write_text(
        "<button onclick=\"localStorage.setItem('b', 'Mysore 07:00'); "
        "location.href='bookings.html'\">Book Mysore</button>", encoding="utf-8")
    (wd / "bookings.html").write_text(
        "<p id=o></p><script>o.textContent = 'Booked: ' + localStorage.getItem('b')</script>",
        encoding="utf-8")
    try:
        out = await stage_check({"page": "index.html", "widths": [390], "text": True,
                                 "clicks": ["Schedule", "Book Mysore"]}, ctx)
        assert "Booked: Mysore 07:00" in out  # localStorage carried across pages
        assert "now on bookings.html" in out and "now on schedule.html" in out
        assert "div.week reaches 2008px" in out  # what sticks out, not only that something does
        assert "texts below contrast 4.5:1" in out and '"Mats left today" 1.' in out
        assert "request failed" not in out and "HTTP 404" not in out  # css/site.css loaded
        version = (await stage.for_org(o.id))[0].version
        await stage_check({"page": "index.html"}, ctx)  # unchanged file: no new Stage version
        assert (await stage.for_org(o.id))[0].version == version
        # A day button reads "Mon19" as textContent; it answers to "Mon 19" as shown, and a
        # miss lists the controls there. A script error says where it was thrown.
        (wd / "days.html").write_text(
            "<p id=o>none</p><button onclick=\"o.textContent='Monday'\"><span>Mon</span>"
            "<span>19</span></button><button aria-label=\"Tue 20, two classes\">T</button>"
            "<script>\nconst DATA = {rooms: {}};\nDATA.rooms.forEach(r => r);\n</script>",
            encoding="utf-8")
        out = await stage_check({"page": "days.html", "clicks": ["Mon 19", "Wed 21"],
                                 "text": True}, ctx)
        assert "Monday" in out
        assert "nothing to click for 'Wed 21' on days.html; its controls: " in out
        assert "DATA.rooms.forEach is not a function (days.html line " in out
    finally:
        await manager.stop()
        await preview.stop()
