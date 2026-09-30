"""Semantic long-term memory: dedup, hybrid search, auto-recall, the Memory API."""

from __future__ import annotations

from app.models import Agent, Memory, ToolCall
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


async def test_memories_are_embedded_deduplicated_and_found(app_client):
    from app.services import memory

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    m1 = await memory.add(org["id"], "The user prefers short answers with examples.",
                          agent_id=ada["id"])
    assert m1.embedding is not None
    # Nearly the same note again: updated, not duplicated.
    m2 = await memory.add(org["id"], "The user prefers short answers with examples!",
                          agent_id=ada["id"])
    assert m2.id == m1.id
    await memory.add(org["id"], "Deploys go through the staging cluster first.", agent_id=None)
    await memory.add(org["id"], "Invoice INV-2291 is overdue.", agent_id=ada["id"])
    assert len(await rows(Memory, Memory.org_id == org["id"])) == 3

    found = await memory.search(org["id"], "what answers does the user prefer?",
                                agent_id=ada["id"], k=3)
    assert found[0].id == m1.id
    exact = await memory.search(org["id"], "INV-2291", agent_id=ada["id"], k=3)
    assert exact and "INV-2291" in exact[0].content  # keywords still work
    shared = await memory.search(org["id"], "staging deploys", agent_id=ada["id"], k=3)
    assert any(m.agent_id is None for m in shared)


async def test_relevant_memories_are_recalled_into_the_turn(app_client):
    from app.services import memory

    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    await memory.add(org["id"], "The user prefers short answers with examples.",
                     agent_id=ada["id"])
    await memory.add(org["id"], "Invoice INV-2291 is overdue.", agent_id=ada["id"])
    prompts = script(ada["id"], call("send_message", to="user", content="ok"), say("done"))
    await dm(app_client, org["id"], "Ada", "Remember how the user prefers answers: short?")
    await wait_for(lambda: _done(ada["id"], 1), msg="turn")
    first = str(prompts[0][0].content)
    assert "# From your long-term memory" in first
    assert "prefers short answers" in first and "INV-2291" not in first
    # Recalled once per turn, carried into later steps of the same turn.
    assert "prefers short answers" in str(prompts[1][0].content)


async def test_memory_api_lists_searches_edits_and_deletes(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    base = f"/api/v1/orgs/{org['id']}/memories"
    r = await app_client.post(base, json={"content": "Standups are at 9:30.",
                                          "agentId": None})
    assert r.status_code == 201 and r.json()["shared"] and r.json()["source"] == "user"
    mine = (await app_client.post(base, json={"content": "Ada owns the billing service.",
                                              "agentId": ada["id"]})).json()
    listing = (await app_client.get(base)).json()
    assert len(listing["items"]) == 2 and listing["counts"] == {"shared": 1, ada["id"]: 1}
    assert listing["embeddings"]["mode"] == "hash"
    only_shared = (await app_client.get(base, params={"scope": "shared"})).json()["items"]
    assert [m["content"] for m in only_shared] == ["Standups are at 9:30."]
    hits = (await app_client.get(base, params={"q": "who owns billing"})).json()["items"]
    assert hits[0]["id"] == mine["id"] and "score" in hits[0]
    edited = await app_client.patch(f"/api/v1/memories/{mine['id']}",
                                    json={"content": "Ada and Ben own billing."})
    assert edited.json()["content"] == "Ada and Ben own billing."
    assert (await app_client.delete(f"/api/v1/memories/{mine['id']}")).status_code == 204
    assert len((await app_client.get(base)).json()["items"]) == 1
