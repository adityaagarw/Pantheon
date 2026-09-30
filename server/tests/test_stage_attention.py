"""Teach-along: the Stage tells an agent what the user is looking at."""

from __future__ import annotations

from app.models import Agent
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows, say, script


async def _page(org_id: str, agent_id: str) -> str:
    from app.services import stage

    p = await stage.save(org_id, agent_id, "Circles", code='const s = new Stage({title: "x"});')
    return p.id


async def _idle(agent_id: str) -> bool:
    return (await rows(Agent, Agent.id == agent_id))[0].runtime_status == "idle"


async def test_attention_reaches_the_teachers_prompt_and_can_be_cleared(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    page = await _page(org["id"], ada["id"])
    base = f"/api/v1/agents/{ada['id']}/attention"

    r = await app_client.post(base, json={"kind": "page", "pageId": page, "held": True,
                                          "text": "Part 2: projections"})
    assert r.status_code == 204
    prompts = script(ada["id"], say("Sure."))
    await dm(app_client, org["id"], "Ada", "Wait, why is it a circle?")
    await wait_for(lambda: len(prompts) >= 1, msg="turn")
    system = str(prompts[0][0].content)
    assert "# The user is with you on the Stage" in system
    assert "Part 2: projections" in system and "PAUSED" in system

    # Once resumed (held: false) the prompt no longer says paused.
    await wait_for(lambda: _idle(ada["id"]), msg="idle")
    await app_client.post(base, json={"kind": "page", "pageId": page, "held": False,
                                      "text": "Part 3"})
    prompts2 = script(ada["id"], say("ok"))
    await dm(app_client, org["id"], "Ada", "thanks")
    await wait_for(lambda: len(prompts2) >= 1, msg="turn 2")
    system = str(prompts2[0][0].content)
    assert "Part 3" in system and "PAUSED" not in system

    # Leaving the Stage clears it.
    assert (await app_client.post(base, json={"kind": "none"})).status_code == 204
    await wait_for(lambda: _idle(ada["id"]), msg="idle 2")
    prompts3 = script(ada["id"], say("ok"))
    await dm(app_client, org["id"], "Ada", "bye")
    await wait_for(lambda: len(prompts3) >= 1, msg="turn 3")
    assert "with you on the Stage" not in str(prompts3[0][0].content)


async def test_browser_and_whiteboard_views_and_validation(app_client):
    from app.services import stage

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    base = f"/api/v1/agents/{ada['id']}/attention"
    r = await app_client.post(base, json={"kind": "browser", "title": "Khan Academy",
                                          "url": "https://www.khanacademy.org/"})
    assert r.status_code == 204
    assert "watching your browser (Khan Academy)" in stage.attention_prompt(ada["id"])
    assert "https://www.khanacademy.org/" in stage.attention_prompt(ada["id"])
    await app_client.post(base, json={"kind": "whiteboard", "title": "Board 1"})
    assert "looking at the whiteboard 'Board 1'" in stage.attention_prompt(ada["id"])

    bad = await app_client.post(base, json={"kind": "hologram"})
    assert bad.status_code == 400
    assert (await app_client.post(base, json={"kind": "page", "pageId": "nope"})).status_code == 404
    ghost = await app_client.post("/api/v1/agents/agt_missing/attention",
                                  json={"kind": "whiteboard", "title": "x"})
    assert ghost.status_code == 404  # unknown agents aren't tracked
    assert stage.attention("agt_missing") is None
