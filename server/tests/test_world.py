"""Physical presence, objects, plugins, assets and the Argus overseer."""

from __future__ import annotations

import json
import struct

from app.models import Agent, Delivery, Message, ToolCall, UserInboxItem, WorldObject
from tests.conftest import wait_for
from tests.helpers import call, dm, last_human, make_agent, make_org, rows, say, script


async def _agent(agent_id: str) -> Agent:
    return (await rows(Agent, Agent.id == agent_id))[0]


async def _done(agent_id: str, n: int = 1) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = await _agent(agent_id)
    return len(calls) >= n and a.runtime_status == "idle"


def _tool_output(prompts, i: int = -1) -> str:
    from langchain_core.messages import ToolMessage

    outs = [str(m.content) for m in prompts[i] if isinstance(m, ToolMessage)]
    return outs[-1] if outs else ""


async def test_agents_know_the_office_and_can_move(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", team="Engineering")
    await make_agent(app_client, org["id"], "Ben", team="Engineering")
    prompts = script(ada["id"], call("look_around"), call("move_to", place="the lounge"),
                     say("I'm in the lounge."))
    await dm(app_client, org["id"], "Ada", "Come to the lounge, please.")
    await wait_for(lambda: _done(ada["id"], 2), msg="moved")

    system = str(prompts[0][0].content)
    assert "# Your surroundings" in system and "Lounge" in system
    seen = _tool_output(prompts, 1)
    assert "Boardroom" in seen and "Kitchen" in seen and "Ben: at their desk" in seen
    assert "You go to the Lounge" in _tool_output(prompts, 2)
    assert (await _agent(ada["id"])).location == {"kind": "room", "room": "Lounge"}
    world = (await app_client.get(f"/api/v1/orgs/{org['id']}/world")).json()
    assert world["locations"][ada["id"]]["room"] == "Lounge"


async def test_speech_is_heard_only_in_the_same_room(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    ben = await make_agent(app_client, org["id"], "Ben")
    cy = await make_agent(app_client, org["id"], "Cy")
    for a, place in ((ada, "Kitchen"), (ben, "Kitchen"), (cy, "Boardroom")):
        r = await app_client.post(f"/api/v1/agents/{a['id']}/move", json={"place": place})
        assert r.status_code == 200, r.text
    ben_prompts = script(ben["id"], say("Morning!"))
    script(cy["id"], say("?"))
    script(ada["id"], call("say_aloud", text="Coffee's ready!"), say("done"))
    await dm(app_client, org["id"], "Ada", "Tell whoever is around that coffee is ready.")
    await wait_for(lambda: _done(ada["id"], 1), msg="spoke")
    await wait_for(lambda: len(ben_prompts) >= 1, msg="ben heard")
    assert "says out loud in the Kitchen" in last_human(ben_prompts[0])
    assert "Coffee's ready!" in last_human(ben_prompts[0])
    cy_deliveries = await rows(Delivery, Delivery.agent_id == cy["id"])
    assert not cy_deliveries


async def test_objects_can_be_picked_up_given_and_must_be_near(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    ben = await make_agent(app_client, org["id"], "Ben")
    r = await app_client.post(f"/api/v1/orgs/{org['id']}/world/objects",
                              json={"name": "mug", "place": {"kind": "room", "room": "Kitchen"}})
    assert r.status_code == 201
    mug = r.json()
    await app_client.post(f"/api/v1/agents/{ben['id']}/move", json={"place": "Kitchen"})
    script(ben["id"], say("thanks"))
    prompts = script(ada["id"], call("pick_up", object="mug"), call("move_to", place="kitchen"),
                     call("pick_up", object="mug"), call("give", object="mug", to="Ben"),
                     say("done"))
    await dm(app_client, org["id"], "Ada", "Bring Ben the mug.")
    await wait_for(lambda: _done(ada["id"], 4), msg="gave")
    assert "move_to there first" in _tool_output(prompts, 1)
    assert "You pick up the mug" in _tool_output(prompts, 3)
    assert "You hand the mug to Ben" in _tool_output(prompts, 4)
    obj = (await rows(WorldObject, WorldObject.id == mug["id"]))[0]
    assert obj.holder_id == ben["id"]


async def test_world_can_be_switched_off(app_client):
    org = await make_org(app_client, settings={"world": {"enabled": False}})
    ada = await make_agent(app_client, org["id"], "Ada")
    prompts = script(ada["id"], say("hi"))
    await dm(app_client, org["id"], "Ada", "hello")
    await wait_for(lambda: len(prompts) >= 1, msg="turn")
    assert "# Your surroundings" not in str(prompts[0][0].content)


async def test_plugin_scenario_template_and_object_behaviour(app_client):
    tpl = (await app_client.get("/api/v1/templates")).json()
    assert any(t["key"] == "town-square/saturday-market" for t in tpl)
    plugins = (await app_client.get("/api/v1/plugins")).json()["plugins"]
    town = next(p for p in plugins if p["id"] == "town-square")
    assert town["error"] is None and "radio_call" in town["tools"]

    r = await app_client.post("/api/v1/orgs", json={"template": "town-square/saturday-market"})
    assert r.status_code == 201, r.text
    org = r.json()
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    by_name = {a["name"]: a for a in snap["agents"]}
    assert by_name["Marco"]["location"] == {"kind": "room", "room": "Market"}
    office = snap["org"]["layout"]["office"]
    desks = [i for i in office["items"] if i.get("agentId")]
    assert {d["agentId"] for d in desks} == {by_name["Officer Reyes"]["id"],
                                            by_name["Officer Kim"]["id"]}
    world = (await app_client.get(f"/api/v1/orgs/{org['id']}/world")).json()
    assert {r["name"] for r in world["rooms"]} >= {"Town Square", "Café", "Police Station",
                                                  "Market", "Alley"}
    knife = next(o for o in world["objects"] if o["name"] == "chef's knife")
    assert knife["holderId"] == by_name["Marco"]["id"]

    reyes = by_name["Officer Reyes"]["id"]
    await app_client.post(f"/api/v1/agents/{reyes}/move", json={"place": "Market"})
    script(by_name["Marco"]["id"], say("Hey!"))
    script(by_name["Priya"]["id"], say("Oh no."))
    prompts = script(reyes, call("use_object", object="handcuffs", action="cuff", target="Marco"),
                     call("radio_call", message="Subject detained at the market."), say("done"))
    await dm(app_client, org["id"], "Officer Reyes", "Detain Marco.")
    await wait_for(lambda: _done(reyes, 2), msg="cuffed")
    assert "Marco is now in handcuffs" in _tool_output(prompts, 1)
    assert "Heard on the radio by: Officer Kim" in _tool_output(prompts, 2)
    cuffs = (await rows(WorldObject, WorldObject.name == "handcuffs"))[0]
    assert cuffs.holder_id == by_name["Marco"]["id"]
    assert cuffs.state == {"restraining": "Marco"}
    # Priya was in the room: witnessing is on in this scenario.
    seen = await rows(Message, Message.kind == "observation")
    assert any("handcuffs" in m.content for m in seen)

    export = (await app_client.get(f"/api/v1/orgs/{org['id']}/export")).json()
    assert export["office"]["autoDesks"] is False
    assert any(o["name"] == "chef's knife" for o in export["objects"])


def _tiny_glb() -> bytes:
    gltf = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
            "nodes": [{"children": [1], "scale": [2, 2, 2]},
                      {"mesh": 0, "translation": [0, 1, 0]}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"componentType": 5126, "count": 3, "type": "VEC3",
                           "min": [-0.5, 0, -0.25], "max": [0.5, 1, 0.25]}]}
    body = json.dumps(gltf).encode()
    body += b" " * (-len(body) % 4)
    return (struct.pack("<4sII", b"glTF", 2, 20 + len(body))
            + struct.pack("<II", len(body), 0x4E4F534A) + body)


async def test_assets_procedural_and_glb_bounds(app_client):
    from app.services import assets

    b = assets.glb_bounds(_tiny_glb())
    assert b == {"min": [-1.0, 2.0, -0.5], "max": [1.0, 4.0, 0.5]}
    scale, size = assets.fit(b, height_m=1.0)
    assert scale == 0.5 and size == [1.0, 0.5, 1.0]

    r = await app_client.post("/api/v1/assets", json={"label": "Crate", "parts": [
        {"shape": "box", "size": [0.5, 0.5, 0.5], "pos": [0, 0.25, 0], "color": "#aa7744"}]})
    assert r.status_code == 201, r.text
    crate = r.json()
    assert crate["key"] == "asset:crate" and crate["size"] == [0.5, 0.5, 0.5]
    bad = await app_client.post("/api/v1/assets", json={"label": "x", "parts": [{"shape": "blob"}]})
    assert bad.status_code == 400
    keys = [a["key"] for a in (await app_client.get("/api/v1/assets")).json()]
    assert "asset:crate" in keys and "plugin:town-square/knife" in keys


async def test_argus_exists_watches_and_reports(app_client):
    from app.services import oversight, supervisor

    argus_id = await supervisor.meta_agent_id("argus")
    zeus_id = await supervisor.meta_agent_id("zeus")
    assert argus_id != zeus_id
    meta = (await app_client.get("/api/v1/meta/argus")).json()
    assert meta["agent"]["name"] == "Argus" and meta["agent"]["metaRole"] == "argus"

    org = await make_org(app_client, name="Watched Co")
    await make_agent(app_client, org["id"], "Ada")
    prompts = script(argus_id, call("org_health", org="Watched Co"),
                     call("raise_alert", org="Watched Co", title="Ada is idle",
                          detail="No tasks assigned.", severity="info"),
                     say("Checked: nothing is moving."))
    r = await app_client.put(f"/api/v1/argus/watches/{org['id']}",
                             json={"everyMinutes": 5, "focus": "progress"})
    assert r.status_code == 200
    # Make the watch due now and run the scheduler once.
    ws = await oversight.watches()
    ws[org["id"]]["lastCheck"] = "2000-01-01T00:00:00+00:00"
    await oversight._save(ws)
    assert await oversight.tick() == 1
    await wait_for(lambda: _done(argus_id, 2), msg="argus checked")
    assert "Scheduled check of Watched Co" in last_human(prompts[0])
    assert "health at" in _tool_output(prompts, 1)
    alerts = await rows(UserInboxItem, UserInboxItem.kind == "alert")
    assert alerts and alerts[0].org_id == org["id"]

    # Deleting the org stops the watch.
    assert (await app_client.delete(f"/api/v1/orgs/{org['id']}")).status_code == 204
    assert org["id"] not in await oversight.watches()
