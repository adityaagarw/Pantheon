"""Templates: small built-ins, Pantheon 1's as a pack that's off until switched on,
and any organization saved as the user's own template, to reuse and share."""

from __future__ import annotations

from tests.helpers import make_agent, use_classic_templates


async def test_built_ins_and_the_classics_on_request(app_client):
    listed = (await app_client.get("/api/v1/templates")).json()
    built_in = {t["key"]: t for t in listed if t["source"] == "builtin"}
    assert set(built_in) == {"solo", "build-team", "research-flow", "research-desk", "app-studio",
                             "content-studio", "data-desk", "tutor", "competitor-scan",
                             "fix-and-review", "ui-design-studio", "ui-design-flow",
                             "brand-studio", "plan-and-build"}
    assert built_in["ui-design-flow"]["mode"] == "flow"
    assert built_in["ui-design-flow"]["flows"] == ["Design and build screens"]
    assert not any(t["key"].startswith("templates-classic/") for t in listed)
    assert built_in["build-team"]["workspace"] == {"git": True, "worktrees": True}
    assert built_in["research-flow"]["mode"] == "flow"
    assert built_in["research-flow"]["flows"] == ["Research report"]

    await use_classic_templates(app_client)
    listed = (await app_client.get("/api/v1/templates")).json()
    assert sum(t["key"].startswith("templates-classic/") for t in listed) == 18
    r = await app_client.patch("/api/v1/packs/templates-classic", json={"enabled": False})
    assert not r.json()["enabled"]


async def test_the_research_flow_template_comes_with_its_flow(app_client):
    org = (await app_client.post("/api/v1/orgs", json={"template": "research-flow"})).json()
    assert org["settings"]["mode"] == "flow"
    flows = (await app_client.get(f"/api/v1/orgs/{org['id']}/flows")).json()
    assert [f["name"] for f in flows] == ["Research report"]
    nodes = flows[0]["graph"]["nodes"]
    assert [n["title"] for n in nodes] == ["Research", "Write the report", "Fact-check and finish"]
    assert [n["x"] for n in nodes] == [0, 320, 640]  # laid out left to right


async def test_an_org_saved_as_a_template_makes_its_like_again(app_client):
    org = (await app_client.post("/api/v1/orgs", json={
        "template": "build-team", "setup": {"source": "new"}})).json()
    await make_agent(app_client, org["id"], "Sam", manager="Mara")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/save-template",
                              json={"name": "My build team", "description": "Mara and three"})
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine == {**mine, "key": "my/my-build-team", "source": "mine", "description": "Mara and three"}
    assert [a["name"] for a in mine["agents"]] == ["Mara", "Kit", "Ren", "Sam"]

    d = (await app_client.get("/api/v1/templates/my/my-build-team")).json()
    assert "git" not in d["settings"]  # where it ran isn't part of it
    assert all(not a.get("worktree") for a in d["agents"])

    again = (await app_client.post("/api/v1/orgs", json={"template": "my/my-build-team"})).json()
    snap = (await app_client.get(f"/api/v1/orgs/{again['id']}")).json()
    assert {a["name"] for a in snap["agents"]} == {"Mara", "Kit", "Ren", "Sam"}
    assert all(a["worktree"] is None for a in snap["agents"])

    # A shared template file is added the same way.
    r = await app_client.post("/api/v1/templates", json={"definition": d, "name": "From a friend"})
    assert r.json()["key"] == "my/from-a-friend"
    assert (await app_client.post("/api/v1/templates", json={"definition": {"x": 1}})).status_code == 400

    assert (await app_client.delete("/api/v1/templates/my/from-a-friend")).status_code == 204
    assert (await app_client.delete("/api/v1/templates/solo")).status_code == 400
    keys = {t["key"] for t in (await app_client.get("/api/v1/templates")).json()}
    assert "my/from-a-friend" not in keys and "my/my-build-team" in keys


async def test_every_built_in_makes_an_org(app_client):
    """Each built-in imports cleanly: its agents, reporting lines and flows, with the world
    off (work teams don't need presence tools)."""
    listed = (await app_client.get("/api/v1/templates")).json()
    for t in [t for t in listed if t["source"] == "builtin"]:
        org = (await app_client.post("/api/v1/orgs", json={"template": t["key"]})).json()
        assert org["settings"]["world"] == {"enabled": False}, t["key"]
        full = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
        names = {a["name"] for a in full["agents"] if not a.get("isSupervisor")}
        # Small teams: every agent pulls weight. A reviewer who builds nothing (an independent
        # check of the whole set: builders checking their own set missed what a fresh eye sees)
        # may join three who build.
        reviewers = {a["name"] for a in full["agents"]
                     if "reviewer" in (a.get("role") or "").lower()}
        assert 1 <= len(names - reviewers) <= 3 and len(reviewers) <= 1, t["key"]
        if t["mode"] == "flow":
            flows = (await app_client.get(f"/api/v1/orgs/{org['id']}/flows")).json()
            assert flows and all(n["agentId"] for n in flows[0]["graph"]["nodes"]), t["key"]


async def test_the_brand_studio_publishes_only_with_approval(app_client):
    org = (await app_client.post("/api/v1/orgs", json={"template": "brand-studio"})).json()
    full = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    pax = next(a for a in full["agents"] if a["name"] == "Pax")
    approval = {t["name"]: t["approval"] for t in pax["tools"]}
    assert all(approval[n] == "ask" for n in ("browser_click", "browser_type", "fetch_url"))
    assert approval["schedule"] == "auto" and approval["md_new"] == "auto"
