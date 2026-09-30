"""Task permissions and meeting settings."""

from __future__ import annotations

from app.llm import mock
from app.models import Meeting, Task, Turn
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script


async def _done(agent_id: str, n: int = 1) -> bool:
    return len(await rows(Turn, Turn.agent_id == agent_id, Turn.status == "completed")) >= n


def _tool_results(prompts):
    return [str(m.content) for m in prompts[-1] if m.type == "tool"]


async def test_assign_scope_reports_only(app_client):
    org = await make_org(app_client)
    lead = await make_agent(app_client, org["id"], "Lead",
                            permissions={"tasks": {"assign": "reports"}})
    await make_agent(app_client, org["id"], "Report", manager=lead["id"])
    await make_agent(app_client, org["id"], "Stranger")
    prompts = script(lead["id"], call("create_task", title="ok one", assignee="Report"),
                     call("create_task", title="not allowed", assignee="Stranger"), say("done"))
    await dm(app_client, org["id"], "Lead", "delegate")
    await wait_for(lambda: _done(lead["id"]), msg="turn")
    results = _tool_results(prompts)
    assert results[0].startswith("Created T-1")
    assert "only assign tasks to yourself or people you manage" in results[1]
    assert [t.title for t in await rows(Task, Task.org_id == org["id"])] == ["ok one"]


async def test_no_create_and_edit_scope(app_client):
    org = await make_org(app_client)
    worker = await make_agent(app_client, org["id"], "Worker",
                              permissions={"tasks": {"create": False, "edit": "assigned"}})
    other = await make_agent(app_client, org["id"], "Other")
    script(other["id"], then=say("ack"))
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/tasks",
                              json={"title": "not yours", "assigneeId": other["id"]})
    assert r.status_code == 201
    prompts = script(worker["id"], call("create_task", title="x"),
                     call("update_task", task="T-1", status="blocked"),
                     call("update_task", task="T-1", comment="just a comment"), say("done"))
    await dm(app_client, org["id"], "Worker", "try things")
    await wait_for(lambda: _done(worker["id"]), msg="turn")
    results = _tool_results(prompts)
    assert "not allowed to create tasks" in results[0]
    assert "may not change T-1" in results[1]
    # Not involved -> even commenting is refused under "assigned".
    assert "may not change T-1" in results[2]


async def test_require_review_blocks_self_closing(app_client):
    org = await make_org(app_client)
    boss = await make_agent(app_client, org["id"], "Boss")
    dev = await make_agent(app_client, org["id"], "Dev")
    script(boss["id"], then=say("noted"))
    await app_client.post(f"/api/v1/orgs/{org['id']}/tasks",
                          json={"title": "build", "assigneeId": dev["id"],
                                "reviewerId": boss["id"]})
    prompts = script(dev["id"], call("update_task", task="T-1", status="done"),
                     call("update_task", task="T-1", status="review", result="built"),
                     say("sent for review"), then=say("ok"))
    await wait_for(lambda: _done(dev["id"]), msg="dev turn")
    results = _tool_results(prompts)
    assert "needs review before it's done" in results[0]
    assert results[1].startswith("Updated T-1 [review]")
    # The org can turn the rule off.
    await app_client.patch(f"/api/v1/orgs/{org['id']}",
                           json={"settings": {"task_policy": {"require_review": False}}})
    r = await app_client.patch(f"/api/v1/agents/{dev['id']}",
                               json={"permissions": {"tasks": {"assign": "nobody"}}})
    assert r.status_code == 400


async def test_meeting_styles_rounds_and_action_item_tasks(app_client):
    org = await make_org(app_client, settings={"meetings": {"max_rounds": 2}})
    lead = await make_agent(app_client, org["id"], "Lead")
    bea = await make_agent(app_client, org["id"], "Bea")
    styles_seen: list[str] = []

    def speaker(messages, tools):
        text = last_human(messages)
        if "Your turn" in text:
            styles_seen.append(str(messages[0].content))
            return say("I can own the schema.")
        if "Attendees:" in text:
            return say("## Decisions\n- Postgres\n## Action items\n- Bea — write the schema\n"
                       'ACTION_ITEMS_JSON\n[{"owner": "Bea", "title": "Write the schema", '
                       '"details": "Design tables"}]')
        return say("noted")

    mock.set_script(lead["id"], speaker)
    mock.set_script(bea["id"], speaker)
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/meetings", json={
        "facilitatorId": lead["id"], "participants": ["Bea"], "agenda": "Pick a DB",
        "style": "decision", "rounds": 5, "createTasks": True})
    assert r.status_code == 202, r.text
    assert r.json()["rounds"] == 2  # capped by the org
    m = (await wait_for(lambda: rows(Meeting, Meeting.status == "done"), msg="meeting"))[0]
    assert m.style == "decision" and "Postgres" in m.minutes
    assert "ACTION_ITEMS_JSON" not in m.minutes
    assert len(styles_seen) == 4 and "converge on a clear decision" in styles_seen[0]
    tasks_ = await rows(Task, Task.org_id == org["id"])
    assert [(t.title, t.assignee_id) for t in tasks_] == [("Write the schema", bea["id"])]
    assert m.task_ids == ["T-1"]
    listed = (await app_client.get(f"/api/v1/orgs/{org['id']}/meetings")).json()
    assert listed[0]["taskIds"] == ["T-1"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/meetings", json={
        "facilitatorId": lead["id"], "participants": ["Bea"], "agenda": "x", "style": "party"})
    assert r.status_code == 400
