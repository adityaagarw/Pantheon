"""Life: residents' needs run down, they look after them by building and using things."""

from __future__ import annotations

from datetime import timedelta

from app.core.ids import utcnow
from app.models import Activity, Agent, Org, Turn
from app.services import life
from tests.conftest import wait_for
from tests.helpers import call, dm, rows, say, script, tool_results, use_classic_templates

WORLD = "templates-classic/world-builders"  # Pantheon 1's living world


def test_needs_run_down_and_set_the_mood():
    org = Org(kind="user", settings={"life": {"enabled": True, "pace": 1}})
    full = {"needs": {n: 100 for n in life.NEEDS}, "at": utcnow().isoformat()}
    assert life.mood(life.needs_now(full, org)) == "great"
    needs = life.needs_now(full, org, utcnow() + timedelta(hours=3))
    assert needs["food"] < needs["energy"] and life.mood(needs) == "good"
    needs = life.needs_now(full, org, utcnow() + timedelta(hours=6))
    assert needs["fun"] == 0 and life.mood(needs) == "miserable"  # bored stiff after 6 hours
    fast = Org(kind="user", settings={"life": {"enabled": True, "pace": 2}})
    assert life.needs_now(full, fast, utcnow() + timedelta(hours=3.5))["food"] == 0
    assert "hungry" in life._feel("food", 20) and life._feel("food", 80) == ""


async def _done(agent_id: str, n: int = 1) -> bool:
    return sum(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id)) >= n


async def test_residents_live_and_build(app_client):
    await use_classic_templates(app_client)
    org = (await app_client.post("/api/v1/orgs", json={"template": WORLD})).json()
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    by_name = {a["name"]: a for a in snap["agents"]}
    assert set(by_name) >= {"Iris", "Mason", "Nova", "Pip", "Fern"}
    fern = by_name["Fern"]
    assert fern["life"]["wants"] and "calm" in fern["life"]["traits"]
    assert all(60 <= v <= 100 for v in fern["life"]["needs"].values())
    acts = await rows(Activity, Activity.org_id == org["id"])
    assert len(acts) == 6 and any(a.title == "Fern's free time" for a in acts)
    assert snap["org"]["layout"]["office"]["open"] is True

    offered: set[str] = set()

    def status(messages, tools):
        offered.update(t.get("name") or t.get("function", {}).get("name") for t in tools)
        return call("life_status")

    prompts = script(
        fern["id"],
        status,
        call("life_do", activity="sleep"),
        call("life_do", activity="wash"),
        call("world_place", item="shower", x=150, z=10),  # past the edge: the open world grows
        call("life_do", activity="wash"),
        call("life_do", activity="chat"),
        call("life_want", action="add", text="a greenhouse"),
        call("life_want", action="done", text="greenhouse"),
        say("lived"), then=say("ok"))
    await dm(app_client, org["id"], "Fern", "how are you?")
    await wait_for(lambda: _done(fern["id"]), timeout=60, msg="Fern finished")
    r = tool_results(prompts[-1])
    assert "Mood:" in r[0] and "sleep: Tent" in r[0] and "(yours)" in r[0]
    assert "wash: nowhere yet" in r[0]
    assert r[1].startswith("You sleep at the Tent") and "(yours)" in r[1]
    assert "nowhere to wash" in r[2] and "shower" in r[2]
    assert r[3].startswith("Placed")
    assert r[4].startswith("You wash at the Shower") and "hygiene" in r[4]
    assert "Nobody" in r[5]
    assert "You now want: a greenhouse" in r[6] and "You made it happen" in r[7]

    # How Fern feels is in Fern's prompt, and the tools came with life.
    system = str(prompts[0][0].content)
    assert "# How you feel" in system and "a vegetable garden" in system
    assert {"life_status", "life_do", "life_want"} <= offered

    after = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    me = next(a for a in after["agents"] if a["name"] == "Fern")
    assert me["life"]["needs"]["hygiene"] >= 70 and me["life"]["done"][-1]["text"] == "a greenhouse"
    assert me["location"]["kind"] == "item" and "Shower" in me["location"]["label"]
    assert after["org"]["layout"]["office"]["width"] >= 154  # grew to fit the shower

    # A plain organization has no life: no needs, no life tools.
    plain = (await app_client.post("/api/v1/orgs", json={"name": "zz-no-life"})).json()
    a = (await app_client.post(f"/api/v1/orgs/{plain['id']}/agents", json={"name": "Ann"})).json()
    assert a["life"] is None
    assert not (await rows(Agent, Agent.id == a["id"]))[0].life
