"""Approve all: every pending request in an org at once."""

from __future__ import annotations

from app.models import Approval, Turn
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _done(agent_id: str) -> bool:
    return any(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id))


async def test_approve_all_unblocks_every_agent(app_client, workspace_root):
    org = await make_org(app_client)
    agents = [await make_agent(app_client, org["id"], n) for n in ("Ann", "Ben")]
    prompts = {a["id"]: script(a["id"], call("run_command", command="echo ran"), say("ok"))
               for a in agents}
    for a in agents:
        await dm(app_client, org["id"], a["name"], "run it")
    await wait_for(lambda: _pending(org["id"], 2), msg="two approvals")

    r = await app_client.post(f"/api/v1/orgs/{org['id']}/approvals/approve-all", json={})
    assert r.json() == {"approved": 2}
    for a in agents:
        await wait_for(lambda a=a: _done(a["id"]), msg=f"{a['name']} continued")
        out = [str(m.content) for m in prompts[a["id"]][1] if m.type == "tool"][0]
        assert "ran" in out
    assert (await app_client.post(f"/api/v1/orgs/{org['id']}/approvals/approve-all",
                                  json={})).json() == {"approved": 0}


async def _pending(org_id: str, n: int) -> bool:
    return len(await rows(Approval, Approval.org_id == org_id, Approval.status == "pending")) >= n


async def test_org_can_run_shell_without_asking(app_client, workspace_root):
    org = await make_org(app_client)
    ann = await make_agent(app_client, org["id"], "Ann")  # run_command defaults to "ask"
    blocked = await make_agent(app_client, org["id"], "Bea",
                               tools=[{"name": "run_command", "approval": "deny"}])
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"shell_approval": "auto"}})
    assert r.status_code == 200 and r.json()["settings"]["shell_approval"] == "auto"

    prompts = script(ann["id"], call("run_command", command="echo straight-through"), say("ok"))
    await dm(app_client, org["id"], "Ann", "run it")
    await wait_for(lambda: _done(ann["id"]), msg="ran without approval")
    assert "straight-through" in [str(m.content) for m in prompts[1] if m.type == "tool"][0]
    assert await rows(Approval, Approval.org_id == org["id"]) == []

    bp = script(blocked["id"], call("run_command", command="echo nope"), say("ok"))
    await dm(app_client, org["id"], "Bea", "run it")
    await wait_for(lambda: _done(blocked["id"]), msg="deny still wins")
    assert "not available to you" in [str(m.content) for m in bp[1] if m.type == "tool"][0]

    bad = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"shell_approval": "yolo"}})
    assert bad.status_code == 400
