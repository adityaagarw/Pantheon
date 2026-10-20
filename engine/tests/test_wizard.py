"""The new-organization wizard: Zeus drafts the team, only real tools survive, and the
edited draft becomes an organization in one call."""

from __future__ import annotations

import json

from app.services.supervisor import supervisor_id
from app.services.wizard import BASE_TOOLS
from tests.helpers import say, script

DRAFT = {"name": "Yoga web", "description": "Build the studio's booking site.",
         "agents": [{"name": "Iva", "role": "Design director", "lead": False,
                     "persona": "You lead.", "tools": ["stage_check", "no_such_tool"]},
                    {"name": "Theo", "role": "Front-end engineer", "persona": "You build.",
                     "tools": ["browser_open"]}],
         "settings": {"max_concurrent": 2}}


async def test_zeus_drafts_a_team_with_real_tools_and_one_lead(app_client):
    script(await supervisor_id(), say("Here it is:\n" + json.dumps(DRAFT)))
    r = await app_client.post("/api/v1/wizard/draft", json={"goal": "a yoga booking site",
                                                            "kind": "team"})
    assert r.status_code == 200, r.text
    d = r.json()
    iva, theo = d["agents"]
    assert iva["lead"] and not theo["lead"]  # a team gets a lead even if Zeus forgot one
    assert iva["tools"] == [*BASE_TOOLS, "stage_check"]  # the made-up tool is dropped
    assert d["settings"] == {"max_concurrent": 2}


async def test_an_edited_draft_becomes_an_organization(app_client, workspace_root):
    definition = {"version": 1, "name": "Yoga web", "description": "d",
                  "settings": {"mode": "team", "shell_approval": "auto", "max_concurrent": 2},
                  "agents": [{"name": "Iva", "role": "Lead", "persona": "You lead.",
                              "tools": [{"name": n, "approval": "auto"} for n in BASE_TOOLS]},
                             {"name": "Theo", "role": "Builder", "persona": "You build.",
                              "tools": [{"name": "read_file", "approval": "auto"}]}],
                  "relationships": [{"from": "Iva", "to": "Theo", "kind": "manages"}]}
    r = await app_client.post("/api/v1/orgs", json={"definition": definition})
    assert r.status_code == 201, r.text
    snap = (await app_client.get(f"/api/v1/orgs/{r.json()['id']}")).json()
    assert sorted(a["name"] for a in snap["agents"]) == ["Iva", "Theo"]
    assert snap["org"]["settings"]["shell_approval"] == "auto"
    assert len(snap["relationships"]) == 1
