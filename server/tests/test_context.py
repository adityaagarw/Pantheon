"""Clearing and compacting an agent's context; the supervisor's name."""

from __future__ import annotations

from langchain_core.messages import SystemMessage

from app.core.db import SessionLocal
from app.models import Agent, Message, Turn
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, rows, say, script


async def _idle_after(agent_id: str, turns: int) -> bool:
    done = await rows(Turn, Turn.agent_id == agent_id, Turn.status == "completed")
    async with SessionLocal() as s:
        agent = await s.get(Agent, agent_id)
    return len(done) >= turns and agent.runtime_status == "idle"


def _text(messages) -> str:
    return "\n".join(str(m.content) for m in messages)


async def _markers(org_id: str, kind: str) -> list[Message]:
    return await rows(Message, Message.org_id == org_id, Message.kind == kind)


async def test_clear_context_forgets_the_conversation(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    prompts = script(ada["id"], say("Noted: the code word is heron."), say("Fresh start."))
    await dm(app_client, org["id"], "Ada", "Remember the code word heron.")
    await wait_for(lambda: _idle_after(ada["id"], 1), msg="first turn")

    r = await app_client.post(f"/api/v1/agents/{ada['id']}/reset-memory")
    assert r.status_code == 200
    thread = (await app_client.get(f"/api/v1/agents/{ada['id']}/thread")).json()
    assert thread["messages"] == [] and thread["context"]["tokens"] == 0
    assert await _markers(org["id"], "context_cleared")

    await dm(app_client, org["id"], "Ada", "What should you do next?")
    await wait_for(lambda: _idle_after(ada["id"], 2), msg="second turn")
    assert "heron" not in _text(prompts[1])
    assert "What should you do next?" in _text(prompts[1])


async def test_compact_now_replaces_history_with_a_summary(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    prompts = script(
        ada["id"],
        say("Noted: the code word is heron."),
        say("SUMMARY: the user set the code word to heron."),  # the compaction call
        say("It's heron."),
    )
    await dm(app_client, org["id"], "Ada", "Remember the code word heron.")
    await wait_for(lambda: _idle_after(ada["id"], 1), msg="first turn")

    r = await app_client.post(f"/api/v1/agents/{ada['id']}/compact")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] >= 1
    thread = (await app_client.get(f"/api/v1/agents/{ada['id']}/thread")).json()
    assert thread["summary"] == "SUMMARY: the user set the code word to heron."
    assert thread["next"] == []  # compaction never starts a turn
    assert await _markers(org["id"], "context_compacted")

    await dm(app_client, org["id"], "Ada", "What is the code word?")
    await wait_for(lambda: _idle_after(ada["id"], 2), msg="second turn")
    turn2 = prompts[2]
    system = next(m for m in turn2 if isinstance(m, SystemMessage))
    assert "SUMMARY: the user set the code word to heron." in str(system.content)
    rest = [m for m in turn2 if not isinstance(m, SystemMessage)]
    assert "Remember the code word heron." not in _text(rest)


async def test_compact_with_nothing_to_compact_is_a_no_op(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    r = await app_client.post(f"/api/v1/agents/{ada['id']}/compact")
    assert r.status_code == 200 and r.json()["removed"] == 0


async def test_supervisor_is_zeus_and_legacy_name_is_migrated(app_client):
    from app.services import orgs, supervisor

    sup = await supervisor.ensure_supervisor()
    assert sup.name == "Zeus"
    await orgs.update_agent(sup.id, {"name": "Pantheon"})
    assert (await supervisor.ensure_supervisor()).name == "Zeus"
    # A name the user picked is kept.
    await orgs.update_agent(sup.id, {"name": "Athena"})
    assert (await supervisor.ensure_supervisor()).name == "Athena"
