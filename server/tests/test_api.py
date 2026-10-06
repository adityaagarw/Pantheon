"""API-level checks: templates, import/export, validation, event catch-up."""

from __future__ import annotations

import os

import pytest

from app.services import templates
from tests.conftest import wait_for
from tests.helpers import dm, make_agent, make_org, say, script


@pytest.mark.parametrize("key", [t["key"] for t in templates.catalog()])
async def test_every_template_builds_a_valid_org(app_client, key):
    r = await app_client.post("/api/v1/orgs", json={"template": key})
    assert r.status_code == 201, r.text
    snap = (await app_client.get(f"/api/v1/orgs/{r.json()['id']}")).json()
    assert len(snap["agents"]) == len(templates.get(key)["agents"])
    for a in snap["agents"]:
        assert a["tools"], a["name"]


async def test_export_import_roundtrip(app_client):
    r = await app_client.post("/api/v1/orgs", json={"template": "software-team", "name": "One"})
    exported = (await app_client.get(f"/api/v1/orgs/{r.json()['id']}/export")).json()
    r2 = await app_client.post("/api/v1/orgs/import", json={"definition": exported,
                                                             "name": "Two"})
    assert r2.status_code == 201
    again = (await app_client.get(f"/api/v1/orgs/{r2.json()['id']}/export")).json()
    assert again["agents"] == exported["agents"]
    assert again["relationships"] == exported["relationships"]


async def test_validation_errors_are_400s(app_client):
    org = await make_org(app_client)
    await make_agent(app_client, org["id"], "Ann")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/agents", json={"name": "ann"})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/agents",
                              json={"name": "Bob", "tools": [{"name": "nope"}]})
    assert r.status_code == 400 and "unknown tool" in r.json()["detail"]
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/agents",
                              json={"name": "Cy", "worktree": "../../../outside"})
    assert r.status_code == 400
    b = await make_agent(app_client, org["id"], "Bea")
    ann = next(a for a in (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["agents"]
               if a["name"] == "Ann")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/relationships",
                              json={"fromId": ann["id"], "toId": b["id"], "kind": "manages"})
    assert r.status_code == 201
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/relationships",
                              json={"fromId": b["id"], "toId": ann["id"], "kind": "manages"})
    assert r.status_code == 400 and "cycle" in r.json()["detail"]
    outside = os.path.abspath(os.sep)  # the filesystem root: outside the workspace roots anywhere
    r = await app_client.post("/api/v1/orgs", json={"name": "X", "workspace": outside})
    assert r.status_code == 400


async def test_events_catch_up_and_turn_trace(app_client):
    org = await make_org(app_client)
    a = await make_agent(app_client, org["id"], "Tracy")
    script(a["id"], say("hello there"))
    await dm(app_client, org["id"], "Tracy", "hi")
    turns = await wait_for(lambda: _completed(app_client, org["id"]), msg="turn")
    detail = (await app_client.get(f"/api/v1/turns/{turns[0]['id']}")).json()
    assert detail["inbox"][0]["content"] == "hi"
    assert detail["sent"][0]["content"] == "hello there"
    call = (await app_client.get(f"/api/v1/llm-calls/{detail['llmCalls'][0]['id']}")).json()
    assert call["request"]["messages"][-1]["content"].endswith("hi")
    events = (await app_client.get(f"/api/v1/orgs/{org['id']}/events?after=0")).json()
    types = [e["type"] for e in events]
    for t in ("message.created", "turn.started", "llm.call", "turn.completed"):
        assert t in types
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    later = (await app_client.get(f"/api/v1/orgs/{org['id']}/events?after={seqs[2]}")).json()
    assert [e["seq"] for e in later] == seqs[3:]
    found = (await app_client.get(f"/api/v1/orgs/{org['id']}/search?q=hello")).json()
    assert found["messages"]
    stats = (await app_client.get(f"/api/v1/orgs/{org['id']}/stats")).json()
    assert stats["agents"][0]["calls"] >= 1


async def _completed(client, org_id):
    ts = (await client.get(f"/api/v1/orgs/{org_id}/turns")).json()
    return [t for t in ts if t["status"] == "completed"]
