"""How many agents work at once: the global limit, per-organization limits, and queueing."""

from __future__ import annotations

import asyncio

from app.agents.slots import Slots
from app.models import Agent, Turn
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _hold(slots: Slots, agent: str, org: str, log: list[str], release: asyncio.Event) -> None:
    async with slots.hold(agent, org):
        log.append(agent)
        await release.wait()


async def test_slots_limit_globally_and_per_org_first_come_first_served():
    slots = Slots(2)
    await slots.set_org_limit("busy", 1)
    log: list[str] = []
    gates = {name: asyncio.Event() for name in "abcde"}
    tasks = [asyncio.create_task(_hold(slots, "a", "busy", log, gates["a"]))]
    await asyncio.sleep(0)
    tasks.append(asyncio.create_task(_hold(slots, "b", "busy", log, gates["b"])))  # org is full
    await asyncio.sleep(0)
    tasks.append(asyncio.create_task(_hold(slots, "c", "other", log, gates["c"])))  # may pass b
    await asyncio.sleep(0.01)
    assert log == ["a", "c"] and slots.status() == {"limit": 2, "running": 2, "waiting": 1}
    tasks.append(asyncio.create_task(_hold(slots, "d", "other", log, gates["d"])))
    await asyncio.sleep(0.01)
    gates["a"].set()  # a frees the busy org's only place: b (waiting longest) goes first
    await asyncio.sleep(0.01)
    assert log == ["a", "c", "b"]
    await slots.set_limit(3)  # more room: d starts right away
    await asyncio.sleep(0.01)
    assert log == ["a", "c", "b", "d"]
    for g in gates.values():
        g.set()
    await asyncio.gather(*tasks)
    assert slots.status()["running"] == 0


async def _turns(agent_id: str) -> int:
    return sum(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id))


async def test_limit_of_one_queues_the_second_agent(app_client):
    from app.agents.runtime import runtime

    r = await app_client.put("/api/v1/runtime/limits", json={"maxConcurrent": 1})
    assert r.json()["maxConcurrent"] == 1
    try:
        org = await make_org(app_client)
        ann = await make_agent(app_client, org["id"], "Ann")
        bob = await make_agent(app_client, org["id"], "Bob")
        # Ann's turn takes a few seconds (a slow command), so Bob has to queue.
        script(ann["id"], call("run_command", command="python -c \"import time; time.sleep(3)\""),
               say("ann done"), then=say("ok"))
        script(bob["id"], say("bob done"), then=say("ok"))
        await app_client.patch(f"/api/v1/agents/{ann['id']}", json={"tools": [
            {"name": "run_command", "approval": "auto"}]})
        await dm(app_client, org["id"], "Ann", "work")
        await wait_for(lambda: _status(ann["id"], "working"), msg="Ann working")
        await dm(app_client, org["id"], "Bob", "work")
        await wait_for(lambda: _status(bob["id"], "queued"), msg="Bob queued")
        limits = (await app_client.get("/api/v1/runtime/limits")).json()
        assert limits["running"] == 1 and limits["waiting"] == 1
        await wait_for(lambda: _turns(bob["id"]), timeout=60, msg="Bob got his turn")
        assert await _turns(ann["id"]) == 1
    finally:
        await runtime.set_limit(8)

    bad = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"max_concurrent": -1}})
    assert bad.status_code == 400
    ok = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"max_concurrent": 2}})
    assert ok.status_code == 200 and runtime.slots.org_limits[org["id"]] == 2


async def _status(agent_id: str, status: str) -> bool:
    return any(a.runtime_status == status for a in await rows(Agent, Agent.id == agent_id))
