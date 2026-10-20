"""Recreating an agent restores its place on the org chart, and Zeus can always see the
whole roster (issue #4)."""

from __future__ import annotations

import json

from app.models import Org, Relationship
from app.services import orgs
from app.tools import base
from tests.helpers import make_agent, make_org, rows


async def test_recreated_agent_takes_over_its_chart_slot_and_reporting_lines(app_client):
    org = await make_org(app_client)
    ava = await make_agent(app_client, org["id"], "Ava")
    keeper = await make_agent(app_client, org["id"], "Keeper")
    vega = await make_agent(app_client, org["id"], "Vega")
    await orgs.set_relationship(org["id"], "Ava", "Vega", "manages")
    await orgs.set_relationship(org["id"], "Vega", "Keeper", "advises")
    slots = {ava["id"]: {"x": 0, "y": 0}, vega["id"]: {"x": 250, "y": 160},
             keeper["id"]: {"x": 500, "y": 160}}
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"layout": {"team": slots}})

    await orgs.delete_agent(vega["id"])
    team = (await rows(Org, Org.id == org["id"]))[0].layout["team"]
    assert vega["id"] not in team  # no ghost slot

    reborn = await orgs.create_agent(org["id"], {"name": "Vega", "role": "SRE"})
    assert reborn.restored_relationships == 2
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    assert snap["org"]["layout"]["team"][reborn.id] == {"x": 250, "y": 160}  # same spot
    rels = {(r.from_id, r.kind, r.to_id) for r in await rows(Relationship, Relationship.org_id == org["id"])}
    assert (ava["id"], "manages", reborn.id) in rels and (reborn.id, "advises", keeper["id"]) in rels


async def test_stale_chart_slots_are_dropped_when_reading(app_client):
    # An org already left stale by older versions is cleaned on read.
    org = await make_org(app_client)
    ava = await make_agent(app_client, org["id"], "Ava")
    team = {ava["id"]: {"x": 1, "y": 2}, "agt_gone": {"x": 9, "y": 9}}
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"layout": {"team": team}})
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    assert snap["org"]["layout"]["team"] == {ava["id"]: {"x": 1, "y": 2}}


async def test_zeus_get_org_lists_every_agent_even_for_a_big_org(app_client):
    org = await make_org(app_client)
    for i in range(25):
        await make_agent(app_client, org["id"], f"Agent{i:02d}", persona="long persona " * 400)
    office = {"version": 1, "width": 40, "depth": 30,
              "items": [{"id": f"i{i}", "kind": "desk", "x": i, "z": i} for i in range(800)]}
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"layout": {"office": office}})

    get_org = base.builtin("get_org").handler
    out = await get_org({"org": org["id"]}, None)
    assert len(out) < 12_000  # fits the tool output limit, so nothing is cut out
    roster = json.loads(out)["agents"]
    assert sorted(a["name"] for a in roster if a["name"].startswith("Agent")) == [
        f"Agent{i:02d}" for i in range(25)]
    assert all(a["id"].startswith("agt_") for a in roster)

    one = json.loads(await get_org({"org": org["id"], "agent": "agent07"}, None))
    assert one["name"] == "Agent07" and one["persona"].startswith("long persona")
