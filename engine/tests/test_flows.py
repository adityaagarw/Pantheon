"""Flows: steps as a directed acyclic graph, each handing its output to the next."""

from __future__ import annotations

import pytest

from app.models import FlowRun
from tests.conftest import wait_for
from tests.helpers import call, last_human, make_agent, make_org, rows, say, script

DIAMOND = {
    "nodes": [
        {"id": "facts", "title": "Gather facts", "agent": "Ada", "instructions": "Find the facts."},
        {"id": "pros", "title": "Argue for", "agent": "Ben"},
        {"id": "cons", "title": "Argue against", "agent": "Cy"},
        {"id": "decide", "title": "Decide", "agent": "Dee", "instructions": "Weigh both sides."},
    ],
    "edges": [{"from": "facts", "to": "pros"}, {"from": "facts", "to": "cons"},
              {"from": "pros", "to": "decide"}, {"from": "cons", "to": "decide"}],
}


async def _team(client) -> dict:
    org = await make_org(client)
    for n in ("Ada", "Ben", "Cy", "Dee"):
        await make_agent(client, org["id"], n, tools=[])
    return org


async def _run(run_id: str) -> FlowRun:
    return (await rows(FlowRun, FlowRun.id == run_id))[0]


async def _status(run_id: str, status: str) -> bool:
    return (await _run(run_id)).status == status


async def test_a_flow_runs_step_by_step_and_hands_outputs_on(app_client):
    org = await _team(app_client)
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "Decide", "graph": DIAMOND})
    assert r.status_code == 201, r.text
    flow = r.json()
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    agents = {a["name"]: a["id"] for a in snap["agents"]}
    assert flow["graph"]["nodes"][0]["agentId"] == agents["Ada"]  # named agents resolve

    seen: dict[str, str] = {}

    def remember(name):
        def step(messages, tools):
            seen[name] = last_human(messages)
            return call("finish_step", output=f"{name}-out")
        return step

    script(agents["Ada"], remember("ada"), say("ok"))
    script(agents["Ben"], remember("ben"), say("ok"))
    # Cy forgets finish_step: the final reply is the output.
    script(agents["Cy"], lambda m, t: (seen.__setitem__("cy", last_human(m)), say("cy-out"))[1])
    script(agents["Dee"], remember("dee"), say("ok"))

    r = await app_client.post(f"/api/v1/flows/{flow['id']}/runs", json={"input": "Should we ship?"})
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["number"] == 1 and run["steps"]["facts"]["status"] == "running"
    assert run["steps"]["decide"]["status"] == "pending"
    await wait_for(lambda: _status(run["id"], "done"), timeout=60, msg="flow done")

    done = await _run(run["id"])
    assert {k: v["output"] for k, v in done.steps.items()} == {
        "facts": "ada-out", "pros": "ben-out", "cons": "cy-out", "decide": "dee-out"}
    assert "# Flow step: Gather facts" in seen["ada"] and "Should we ship?" in seen["ada"]
    assert "Find the facts." in seen["ada"] and "finish_step" in seen["ada"]
    assert "ada-out" in seen["ben"] and "ada-out" in seen["cy"]
    assert "ben-out" in seen["dee"] and "cy-out" in seen["dee"] and "Weigh both sides." in seen["dee"]
    assert "Gather facts" not in seen["dee"].split("From the steps before yours")[1]  # only its inputs
    listed = (await app_client.get(f"/api/v1/flows/{flow['id']}/runs")).json()
    assert [x["status"] for x in listed] == ["done"]


async def test_a_failed_step_skips_what_follows_and_runs_can_be_cancelled(app_client):
    org = await _team(app_client)
    flow = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows",
                                  json={"name": "Decide", "graph": DIAMOND})).json()
    ada = flow["graph"]["nodes"][0]["agentId"]
    script(ada, call("finish_step", output="no data available", failed=True), say("ok"))
    run = (await app_client.post(f"/api/v1/flows/{flow['id']}/runs", json={"input": "x"})).json()
    await wait_for(lambda: _status(run["id"], "failed"), timeout=30, msg="flow failed")
    steps = (await _run(run["id"])).steps
    assert steps["facts"] == {**steps["facts"], "status": "failed", "error": "no data available"}
    assert {steps[k]["status"] for k in ("pros", "cons", "decide")} == {"skipped"}

    # A run stopped by the user: what hadn't finished is skipped.
    script(ada, say("thinking"), then=say("ok"))
    run = (await app_client.post(f"/api/v1/flows/{flow['id']}/runs", json={"input": "y"})).json()
    assert run["number"] == 2
    r = await app_client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    assert {s["status"] for s in r.json()["steps"].values()} == {"skipped"}


async def test_flows_only_go_forward(app_client):
    org = await _team(app_client)
    loop = {"nodes": [{"id": "a", "agent": "Ada"}, {"id": "b", "agent": "Ben"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "Loop", "graph": loop})
    assert r.status_code == 400 and "loop back" in r.json()["detail"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "X", "graph": {
        "nodes": [{"id": "a", "agent": "Nobody"}], "edges": []}})
    assert r.status_code == 400 and "needs one of this organization's agents" in r.json()["detail"]
    empty = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "Empty"})).json()
    r = await app_client.post(f"/api/v1/flows/{empty['id']}/runs", json={})
    assert r.status_code == 400 and "no steps" in r.json()["detail"]
    r = await app_client.patch(f"/api/v1/flows/{empty['id']}", json={"graph": loop})
    assert r.status_code == 400


async def test_a_flow_mode_org_works_only_through_flows(app_client):
    org = await _team(app_client)
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"mode": "flow"}})
    assert r.json()["settings"]["mode"] == "flow"
    assert (await app_client.patch(f"/api/v1/orgs/{org['id']}",
                                   json={"settings": {"mode": "chaos"}})).status_code == 400
    flow = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "One", "graph": {
        "nodes": [{"id": "a", "agent": "Ada"}], "edges": []}})).json()
    systems: list[str] = []

    def step(messages, tools):
        systems.append(str(messages[0].content))
        return call("finish_step", output="ok")

    script(flow["graph"]["nodes"][0]["agentId"], step, say("done"))
    run = (await app_client.post(f"/api/v1/flows/{flow['id']}/runs", json={})).json()
    await wait_for(lambda: _status(run["id"], "done"), timeout=30, msg="run done")
    assert "# How this organization works: flows" in systems[0]
    assert "# Working well as a team" not in systems[0]


async def test_in_a_flow_mode_org_what_the_user_says_runs_the_flow(app_client):
    """Typing the goal to an agent in the Console runs the flow (it used to be chat for that
    agent alone), and the last step's result comes back in that conversation."""
    from app.models import Message, UserInboxItem

    org = await _team(app_client)
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"mode": "flow"}})
    flow = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows", json={"name": "Pipe", "graph": {
        "nodes": [{"id": "a", "title": "Find", "agent": "Ada"},
                  {"id": "b", "title": "Write", "agent": "Ben"}],
        "edges": [{"from": "a", "to": "b"}]}})).json()
    ada, ben = (n["agentId"] for n in flow["graph"]["nodes"])
    got: dict[str, str] = {}

    def asked(messages, tools):
        got["ada"] = last_human(messages)
        return call("finish_step", output="notes")

    script(ada, asked, say("ok"))
    script(ben, call("finish_step", output="THE REPORT"), say("ok"))

    r = await app_client.post(f"/api/v1/orgs/{org['id']}/messages",
                              json={"to": "Ada", "content": "Top Roblox games?"})
    assert r.status_code == 201, r.text
    dm = r.json()["channelId"]
    runs = await rows(FlowRun, FlowRun.org_id == org["id"])
    assert len(runs) == 1 and runs[0].input == "Top Roblox games?"
    await wait_for(lambda: _status(runs[0].id, "done"), timeout=30, msg="run done")
    assert "# Flow step: Find" in got["ada"] and "Top Roblox games?" in got["ada"]

    async def answered() -> bool:  # posted just after the run is marked done
        return bool(await rows(Message, Message.channel_id == dm, Message.kind == "reply"))

    await wait_for(answered, timeout=10, msg="answer posted")
    said = [(m.sender_id, m.kind, m.content) for m in sorted(
        await rows(Message, Message.channel_id == dm), key=lambda m: m.created_at)]
    assert said[0] == ("user", "chat", "Top Roblox games?")
    assert said[1][1] == "flow_run" and "Find → Write" in said[1][2]
    assert said[-1] == (ben, "reply", "THE REPORT")
    async def in_inbox() -> bool:  # saved just after the answer
        return any("THE REPORT" in i.title for i in await rows(
            UserInboxItem, UserInboxItem.org_id == org["id"]))

    await wait_for(in_inbox, timeout=10, msg="answer in the Inbox")

    # Talking to an agent in the middle of its step reaches it, and starts nothing. (Paused,
    # Ada's step stays running however fast she'd be.)
    await app_client.patch(f"/api/v1/agents/{ada}", json={"status": "paused"})
    await flows_run(app_client, flow["id"])
    await app_client.post(f"/api/v1/orgs/{org['id']}/messages",
                          json={"to": "Ada", "content": "use 2026 data"})
    assert len(await rows(FlowRun, FlowRun.org_id == org["id"])) == 2


async def flows_run(client, flow_id: str) -> None:
    run = (await client.post(f"/api/v1/flows/{flow_id}/runs", json={"input": "again"})).json()
    assert run["steps"]["a"]["status"] == "running"


def test_zeus_lays_out_the_flows_it_makes():
    from app.services.flows import layout

    placed = {s["id"]: (s["x"], s["y"]) for s in layout(
        [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}],
        [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}, {"from": "b", "to": "d"},
         {"from": "c", "to": "d"}])}
    assert placed == {"a": (0, 0), "b": (320, 0), "c": (320, 190), "d": (640, 0)}


async def test_a_lead_runs_the_goal_as_a_flow_it_plans_and_hears_when_it_ends(app_client):
    # A team on tasks and messages took 70 minutes where one agent took 10: a lead now plans
    # a flow for the goal in hand, the steps run without chatter, and the lead is told when
    # the run ends, to hand the goal in.
    from tests.helpers import dm

    org = await make_org(app_client)
    iva = await make_agent(app_client, org["id"], "Iva")
    theo = await make_agent(app_client, org["id"], "Theo", tools=[])
    uma = await make_agent(app_client, org["id"], "Uma", tools=[])
    for who in (theo, uma):
        await app_client.post(f"/api/v1/orgs/{org['id']}/relationships",
                              json={"fromId": iva["id"], "toId": who["id"], "kind": "manages"})
    told: list[str] = []

    def lead_hears(messages, tools):
        told.append(last_human(messages))
        return say("handing in")

    script(iva["id"],
           call("create_task", title="Booking app", goal=True),
           call("plan_flow", name="Booking app", steps=[
               {"id": "spec", "agent": "Iva", "instructions": "Write DESIGN.md."},
               {"id": "a", "agent": "Theo", "instructions": "Build schedule.html."},
               {"id": "b", "agent": "Uma", "instructions": "Build my-bookings.html."}],
               edges=[{"from": "spec", "to": "a"}, {"from": "spec", "to": "b"}]),
           say("planned"),
           call("finish_step", output="spec-out"), say("ok"),
           then=lead_hears)
    script(theo["id"], call("finish_step", output="schedule built"), say("ok"))
    script(uma["id"], call("finish_step", output="bookings built"), say("ok"))
    await dm(app_client, org["id"], "Iva", "Design a booking app for yoga studios.")

    async def told_of_the_end() -> bool:
        return any("finished" in t for t in told)

    await wait_for(told_of_the_end, timeout=30, msg="the lead heard the run end")
    run = (await rows(FlowRun, FlowRun.org_id == org["id"]))[0]
    assert run.status == "done" and run.input.startswith("Design a booking app")
    end = next(t for t in told if "finished" in t)
    assert "schedule built" in end and "bookings built" in end and "T-1" in end


async def test_each_step_owns_its_files_while_others_build(app_client, workspace_root):
    """A timed run lost most of its hour to builders working the same files: one generated
    every screen from his own parts, overwriting his colleagues', who spent the rest
    reconciling. A step's files are its own while it runs (named, or written first), and a
    finished step's stay as they are while other people's steps build on them."""
    import sys

    from app.models import Agent, Org
    from app.services import flows
    from app.tools import base, resolve  # noqa: F401  (registers the built-in tools)
    from app.tools.base import ToolContext, ToolError, org_workspace

    org = await _team(app_client)
    graph = {"nodes": [
        {"id": "spec", "title": "Spec", "agent": "Ada", "files": ["DESIGN.md"]},
        {"id": "a", "title": "Schedule", "agent": "Ben"},
        {"id": "b", "title": "Bookings", "agent": "Cy"},
        {"id": "check", "title": "Check", "agent": "Dee", "files": ["NOTES.md"]}],
        "edges": [{"from": "spec", "to": "a"}, {"from": "spec", "to": "b"},
                  {"from": "a", "to": "check"}, {"from": "b", "to": "check"}]}
    flow = (await app_client.post(f"/api/v1/orgs/{org['id']}/flows",
                                  json={"name": "Build", "graph": graph})).json()
    assert flow["graph"]["nodes"][0]["files"] == ["DESIGN.md"]
    o = (await rows(Org, Org.id == org["id"]))[0]
    ctx = {}
    for a in await rows(Agent, Agent.org_id == org["id"]):
        await app_client.patch(f"/api/v1/agents/{a.id}", json={"status": "paused"})
        ctx[a.name] = ToolContext(org=o, agent=a, turn_id=None, tool_call_id="t", depth=0)
    write = base.builtin("write_file").handler
    edit = base.builtin("edit_file").handler
    shell = base.builtin("run_command").handler
    run = await flows.start(flow["id"], "Build a booking app")

    await write({"path": "DESIGN.md", "content": "tokens"}, ctx["Ada"])
    with pytest.raises(ToolError, match="Ada is making it in the step 'Spec'"):
        await write({"path": "DESIGN.md", "content": "mine"}, ctx["Ben"])

    await flows.finish_step(run.id, "spec", "spec done")
    await write({"path": "schedule.html", "content": "<p>Ben's</p>"}, ctx["Ben"])  # first: his
    with pytest.raises(ToolError, match="Ben is making it in the step 'Schedule'"):
        await write({"path": "schedule.html", "content": "<p>Cy's</p>"}, ctx["Cy"])
    with pytest.raises(ToolError, match="came from the step 'Spec', and Cy build on it"):
        await edit({"path": "DESIGN.md", "old": "tokens", "new": "colours"}, ctx["Ben"])
    # A command can't change them either: what it changed is put back.
    out = await shell({"command": f'"{sys.executable}" -c "open(\'schedule.html\', \'w\')'
                                  f'.write(\'regenerated\')"'}, ctx["Cy"])
    ws = org_workspace(o)
    assert "put back schedule.html" in out
    assert (ws / "schedule.html").read_text(encoding="utf-8") == "<p>Ben's</p>"
    await write({"path": "my-bookings.html", "content": "<p>Cy's</p>"}, ctx["Cy"])
    await shell({"command": f'"{sys.executable}" -c "open(\'cy.css\', \'w\').write(\'p{{}}\')"'},
                ctx["Cy"])

    # Once the builders are done, the check step fixes across everyone's files. Each result
    # carries Pantheon's record of the files the step changed (a builder's result claimed
    # fixes in colleagues' pages it never touched), its commands' changes included.
    await flows.finish_step(run.id, "a", "schedule, and I fixed my-bookings.html too")
    await flows.finish_step(run.id, "b", "bookings")
    await edit({"path": "schedule.html", "old": "Ben's", "new": "fixed"}, ctx["Dee"])
    await edit({"path": "DESIGN.md", "old": "tokens", "new": "colours"}, ctx["Dee"])
    steps = (await _run(run.id)).steps
    assert steps["a"]["made"] == ["schedule.html"] and steps["b"]["made"] == ["my-bookings.html"]
    assert steps["a"]["output"].endswith("Files this step changed (Pantheon's record): "
                                         "schedule.html")
    assert "my-bookings.html, cy.css" in steps["b"]["output"]
    assert "schedule.html" not in steps["b"]["output"]  # put back: not Cy's change
