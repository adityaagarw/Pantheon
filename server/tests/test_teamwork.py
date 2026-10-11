"""Teams that finish: leads get lead rules, waiting work doesn't wake anyone, done means stop."""

from __future__ import annotations

from app.agents.prompt import LEAD_RULES, TEAM_RULES, build_system_prompt
from app.models import Agent, Delivery, Message, Org
from app.services import tasks
from app.services.comms import Sender
from tests.helpers import make_agent, make_org, rows


async def _team(app_client):
    org = await make_org(app_client, "Crew")
    lead = await make_agent(app_client, org["id"], "Lia", persona="You lead.")
    dev = await make_agent(app_client, org["id"], "Dev")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/relationships",
                              json={"fromId": lead["id"], "toId": dev["id"], "kind": "manages"})
    assert r.status_code == 201
    return org, lead, dev


async def _prompt(org_id: str, agent_id: str) -> str:
    org = (await rows(Org, Org.id == org_id))[0]
    agent = (await rows(Agent, Agent.id == agent_id))[0]
    return await build_system_prompt(org, agent)


async def _woken(agent_id: str, text: str) -> bool:
    msgs = await rows(Message, Message.content.contains(text))
    ids = [m.id for m in msgs]
    return bool(ids) and bool(await rows(Delivery, Delivery.agent_id == agent_id,
                                         Delivery.message_id.in_(ids)))


async def test_leads_get_lead_rules_and_everyone_gets_team_rules(app_client):
    org, lead, dev = await _team(app_client)
    lead_prompt, dev_prompt = await _prompt(org["id"], lead["id"]), await _prompt(org["id"], dev["id"])
    assert TEAM_RULES in lead_prompt and LEAD_RULES in lead_prompt
    assert TEAM_RULES in dev_prompt and LEAD_RULES not in dev_prompt
    solo = await make_org(app_client, "Solo")
    one = await make_agent(app_client, solo["id"], "Only")
    assert TEAM_RULES not in await _prompt(solo["id"], one["id"])  # nobody to work with
    village = (await app_client.post("/api/v1/orgs", json={"template": "world-builders"})).json()
    resident = (await rows(Agent, Agent.org_id == village["id"]))[0]
    assert TEAM_RULES not in await _prompt(village["id"], resident.id)  # living, not a project


async def test_waiting_work_is_quiet_until_it_can_start(app_client, monkeypatch):
    from app.agents import signals

    monkeypatch.setattr(signals, "wake", lambda *_: None)  # look at deliveries, not turns
    org, lead, dev = await _team(app_client)
    me = Sender("agent", lead["id"])
    fix = await tasks.create_task(org["id"], me, title="Fix it", assignee="Lia")
    check = await tasks.create_task(org["id"], me, title="Check it", assignee="Dev",
                                    depends_on=[f"T-{fix.number}"])
    assert not await _woken(dev["id"], "assigned you T-2")  # recorded, not a wake-up
    assert "waits on T-1 (don't start it yet)" in await _prompt(org["id"], dev["id"])

    # The lead lets it start early by removing the dependency: now Dev is told.
    await tasks.update_task(org["id"], me, f"T-{check.number}", depends_on=[])
    assert await _woken(dev["id"], "no longer waits on other tasks")
    assert "don't start it yet)" not in await _prompt(org["id"], dev["id"])


async def test_you_can_start_comes_once_and_not_to_someone_already_working(app_client, monkeypatch):
    from app.agents import signals

    monkeypatch.setattr(signals, "wake", lambda *_: None)
    org, lead, dev = await _team(app_client)
    me = Sender("agent", lead["id"])
    await tasks.create_task(org["id"], me, title="Fix it", assignee="Lia")
    await tasks.create_task(org["id"], me, title="Check it", assignee="Dev", depends_on=["T-1"])
    await tasks.update_task(org["id"], me, "T-1", status="done")
    assert len(await rows(Message, Message.content.contains("are done (last: T-1)"))) == 1
    await tasks.update_task(org["id"], Sender("agent", dev["id"]), "T-2", status="in_progress")
    # The fix is reopened and closed again while Dev is already checking it: no second "go".
    await tasks.update_task(org["id"], me, "T-1", status="review")
    await tasks.update_task(org["id"], me, "T-1", status="done")
    assert len(await rows(Message, Message.content.contains("are done (last: T-1)"))) == 1


async def test_standby_waits_for_work_it_can_do(app_client, monkeypatch):
    from app.agents import signals
    from app.services import comms

    monkeypatch.setattr(signals, "wake", lambda *_: None)
    org, lead, dev = await _team(app_client)
    r = await app_client.patch(f"/api/v1/agents/{dev['id']}", json={"status": "standby"})
    assert r.status_code == 200 and r.json()["status"] == "standby"
    me = Sender("agent", lead["id"])

    async def status() -> str:
        return (await rows(Agent, Agent.id == dev["id"]))[0].status

    # Chat and a task that still waits: Dev stays on standby (the chat waits for it).
    await comms.send_direct(org["id"], me, "Dev", "FYI: the design is half done.")
    await tasks.create_task(org["id"], me, title="Build it", assignee="Lia")
    await tasks.create_task(org["id"], me, title="Check it", assignee="Dev", depends_on=["T-1"])
    assert await status() == "standby"
    # The work it waits on is done: now Dev has something to do, and comes back.
    await tasks.update_task(org["id"], me, "T-1", status="done")
    assert await status() == "active"


async def test_a_finished_goal_tells_people_on_open_subtasks_to_stop(app_client, monkeypatch):
    from app.agents import signals

    monkeypatch.setattr(signals, "wake", lambda *_: None)
    org, lead, dev = await _team(app_client)
    user = Sender.user()
    goal = await tasks.create_task(org["id"], user, title="Ship it", assignee="Lia")
    await tasks.create_task(org["id"], Sender("agent", lead["id"]), title="Polish docs",
                            assignee="Dev", parent=f"T-{goal.number}")
    await tasks.update_task(org["id"], user, f"T-{goal.number}", status="done")
    assert await _woken(dev["id"], "is no longer needed. Stop work on it")
