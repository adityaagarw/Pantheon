"""Channels can be repaired: members edited, renamed, archived, deleted, and an agent that is
deleted and recreated gets its memberships back (issue #5)."""

from __future__ import annotations

from app.models import Channel
from app.services import comms, orgs
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _channel(org_id: str, key: str) -> Channel | None:
    found = await rows(Channel, Channel.org_id == org_id, Channel.key == key)
    return found[0] if found else None


async def test_api_edits_renames_archives_and_deletes(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    bob = await make_agent(app_client, org["id"], "Bob")
    ch = (await app_client.post(f"/api/v1/orgs/{org['id']}/channels",
                                json={"name": "ops", "members": ["Ada"]})).json()

    r = await app_client.patch(f"/api/v1/channels/{ch['id']}", json={"add": ["Bob"], "remove": ["Ada"]})
    assert r.status_code == 200 and r.json()["members"] == [bob["id"]]
    r = await app_client.patch(f"/api/v1/channels/{ch['id']}", json={"name": "Ops Alerts"})
    assert r.json()["key"] == "#ops-alerts"

    # Archiving frees the name for a replacement, and the old one is clearly superseded.
    r = await app_client.patch(f"/api/v1/channels/{ch['id']}", json={"archived": True})
    assert r.json()["archived"] is True and r.json()["key"].startswith("#ops-alerts-archived-")
    again = await app_client.post(f"/api/v1/orgs/{org['id']}/channels",
                                  json={"name": "ops-alerts", "members": ["Ada", "Bob"]})
    assert again.status_code == 201 and again.json()["key"] == "#ops-alerts"

    # A live channel's name is taken, with a pointer to the fix.
    dup = await app_client.post(f"/api/v1/orgs/{org['id']}/channels",
                                json={"name": "ops-alerts", "members": []})
    assert dup.status_code == 400 and "update_channel" in dup.json()["detail"]

    assert (await app_client.delete(f"/api/v1/channels/{ch['id']}")).status_code == 204
    assert await rows(Channel, Channel.id == ch["id"]) == []
    general = await _channel(org["id"], "#general")
    assert (await app_client.delete(f"/api/v1/channels/{general.id}")).status_code == 400
    assert ada  # keep the fixture readable


async def test_agents_manage_channels_they_belong_to(app_client):
    org = await make_org(app_client)
    await make_agent(app_client, org["id"], "Ava")
    keeper = await make_agent(app_client, org["id"], "Keeper")
    outsider = await make_agent(app_client, org["id"], "Out")
    await comms.create_channel(org["id"], "alerts", ["Keeper"])

    prompts = script(keeper["id"], call("update_channel", channel="alerts",
                                        add_members=["Ava", "user"], topic="pager"), say("ok"))
    await dm(app_client, org["id"], "Keeper", "add Ava")
    await wait_for(lambda: _has_tool_result(prompts, 1), msg="keeper turn")
    assert "added Ava" in _tool_out(prompts, 1) and "added you (the user)" in _tool_out(prompts, 1)

    prompts = script(outsider["id"], call("update_channel", channel="alerts", archive=True), say("ok"))
    await dm(app_client, org["id"], "Out", "archive alerts")
    await wait_for(lambda: _has_tool_result(prompts, 1), msg="outsider turn")
    assert "only members of #alerts can change it" in _tool_out(prompts, 1)
    assert not (await _channel(org["id"], "#alerts")).archived


async def test_recreated_agent_gets_its_channels_back(app_client):
    org = await make_org(app_client)
    vega = await make_agent(app_client, org["id"], "Vega")
    await comms.create_channel(org["id"], "sre", ["Vega", "user"])
    await comms.create_channel(org["id"], "approvals", ["Vega"])
    await orgs.delete_agent(vega["id"])
    assert vega["id"] not in (await _channel(org["id"], "#sre")).members

    reborn = await orgs.create_agent(org["id"], {"name": "vega", "role": "SRE"})
    assert sorted(reborn.restored_channels) == ["#approvals", "#sre"]
    for key in ("#sre", "#approvals"):
        assert reborn.id in (await _channel(org["id"], key)).members
    # Recreating again restores again, following a channel even after it was archived.
    await orgs.delete_agent(reborn.id)
    await comms.update_channel(org["id"], "approvals", archived=True)
    third = await orgs.create_agent(org["id"], {"name": "Vega", "role": "SRE"})
    assert sorted(third.restored_channels) == [f"#approvals-archived-{_today()}", "#sre"]
    # A brand-new name gets nothing extra.
    other = await orgs.create_agent(org["id"], {"name": "Nova", "role": "SRE"})
    assert other.restored_channels == []


def _today() -> str:
    from app.core.ids import utcnow
    return f"{utcnow():%Y-%m-%d}"


def _has_tool_result(prompts, i: int) -> bool:
    async def check() -> bool:
        return len(prompts) > i
    return check()


def _tool_out(prompts, i: int) -> str:
    return [str(m.content) for m in prompts[i] if m.type == "tool"][-1]
