"""What keeps a long run on track: a checklist, the clock, batching notes, forgiving task
tools, and stage_check finding dead links and walking a flow."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agents import nudges
from app.agents.prompt import build_system_prompt
from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.models import Agent, Org, Task
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _done(agent_id: str) -> bool:
    from app.models import Turn

    return bool(await rows(Turn, Turn.agent_id == agent_id, Turn.status == "completed"))


async def test_the_checklist_is_kept_and_shown_each_turn(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    prompts = script(ann["id"], call("todo", items=[
        {"text": "Write primes.py", "status": "done"},
        {"text": "Write the tests", "status": "in_progress"},
        {"text": "Run them", "status": "pending"}]), say("on it"))
    await dm(app_client, org["id"], "Ann", "go")
    await wait_for(lambda: _done(ann["id"]), msg="turn")
    result = [str(m.content) for m in prompts[-1] if m.type == "tool"][0]
    assert result.startswith("Checklist (1 of 3 done.)") and "[>] Write the tests" in result
    a = (await rows(Agent, Agent.id == ann["id"]))[0]
    o = (await rows(Org, Org.id == org["id"]))[0]
    assert "# Your checklist" in await build_system_prompt(o, a)


def _single(i: int) -> list:
    return [AIMessage("", tool_calls=[{"id": f"c{i}", "name": "read_file", "args": {}}]),
            ToolMessage("x", tool_call_id=f"c{i}")]


async def test_notes_batching_and_a_checklist_once_each():
    agent = SimpleNamespace(id="agt_x", org_id="org_none", todos=[])
    msgs = [HumanMessage("go"), *[m for i in range(4) for m in _single(i)]]
    lines, noted = await nudges.notes(agent, msgs, 12, {})
    assert any("one tool call" in x for x in lines) and any("checklist" in x for x in lines)
    again, _ = await nudges.notes(agent, msgs, 13, noted)
    assert again == []  # rate-limited


async def test_notes_tell_the_time_on_a_goal_with_a_deadline(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/tasks", json={"title": "The goal"})
    async with SessionLocal() as session:
        t = await session.get(Task, r.json()["id"])
        t.created_at, t.due_at = utcnow() - timedelta(minutes=52), utcnow() + timedelta(minutes=8)
        await session.commit()
    agent = (await rows(Agent, Agent.id == ann["id"]))[0]
    lines, _ = await nudges.notes(agent, [], 3, {})
    assert "Time: 52 of 60 minutes used on T-1 (8 left). Finish what is in progress" in lines[0]


async def test_create_task_takes_a_status_and_leaves_out_made_up_dependencies(app_client):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")
    prompts = script(ann["id"], call("create_task", title="Spec", status="done", result="SPEC.md",
                                     depends_on=["S-0"]), say("ok"))
    await dm(app_client, org["id"], "Ann", "go")
    await wait_for(lambda: _done(ann["id"]), msg="turn")
    result = [str(m.content) for m in prompts[-1] if m.type == "tool"][0]
    assert result == "Created T-1: Spec [done]. No task S-0: left out of depends_on."


@pytest.fixture
async def page():
    playwright = pytest.importorskip("playwright.async_api")
    async with playwright.async_playwright() as p:
        try:
            browser = await p.chromium.launch()
        except Exception:  # noqa: BLE001
            pytest.skip("no Chromium")
        yield await browser.new_page()
        await browser.close()


async def test_stage_check_finds_dead_links_and_clicks_through_a_flow(page):
    from app.tools.stagetools import CLICK, LINKS

    await page.set_content("""
      <a href="#book">Book</a> <a href="#nowhere">Gone</a> <a href="schedule.html#mon">Schedule</a>
      <a href="https://example.com">Out</a>
      <section id="book"><button onclick="document.body.dataset.step='paid'">Pay now</button></section>""")
    links = await page.evaluate(LINKS)
    assert links == {"missing": ["#nowhere"], "files": ["schedule.html"]}
    assert await page.evaluate(CLICK, "pay now") is True
    assert await page.evaluate("document.body.dataset.step") == "paid"
    # Nothing to click: the controls that are there, to pick from.
    assert "pay now" in await page.evaluate(CLICK, "Refund")
