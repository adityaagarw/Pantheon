"""Shared Excalidraw whiteboards: merging, agents drawing and reading, erasing."""

from __future__ import annotations

from app.models import Agent, ToolCall
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


def el(eid: str, version: int, nonce: int = 1, **kw):
    return {"id": eid, "type": "rectangle", "x": 0, "y": 0, "width": 100, "height": 50,
            "version": version, "versionNonce": nonce, "isDeleted": False, **kw}


def test_merge_keeps_the_newest_version_of_each_element():
    from app.services.whiteboards import merge

    stored = [el("a", 3), el("b", 1)]
    merged, changed = merge(stored, [el("a", 2, x=99), el("b", 2, x=5), el("c", 1)])
    by = {e["id"]: e for e in merged}
    assert changed and by["a"]["version"] == 3 and by["b"]["x"] == 5 and "c" in by
    assert [e["id"] for e in merged] == ["a", "b", "c"]
    # Same version: the lower versionNonce wins (Excalidraw's rule).
    merged, _ = merge([el("a", 3, nonce=50)], [el("a", 3, nonce=10, x=7)])
    assert merged[0]["x"] == 7
    assert merge(merged, merged)[1] is False


async def test_agents_draw_read_and_erase(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    boards = (await app_client.get(f"/api/v1/orgs/{org['id']}/whiteboards")).json()
    assert len(boards) == 1 and boards[0]["title"] == "Whiteboard"  # created on demand
    bid = boards[0]["id"]
    # The user drew a box, which Excalidraw saved.
    user_box = el("u1", 1, x=0, y=0)
    label = {"id": "u1t", "type": "text", "text": "Frontend", "containerId": "u1", "x": 10,
             "y": 10, "version": 1, "versionNonce": 1, "isDeleted": False}
    r = await app_client.put(f"/api/v1/whiteboards/{bid}/scene",
                             json={"elements": [user_box, label]})
    assert r.json()["version"] == 2

    ap = script(ada["id"], call("whiteboard_read"),
                call("whiteboard_draw", shapes=[
                    {"type": "rectangle", "id": "api", "label": "API"},
                    {"type": "ellipse", "label": "Database", "fill": "#a5d8ff"},
                    {"type": "arrow", "from": "Frontend", "to": "api", "label": "HTTP"},
                    {"type": "arrow", "from": "api", "to": "database"}]),
                call("whiteboard_read"), say("drawn"))
    await dm(app_client, org["id"], "Ada", "Sketch the architecture next to my box.")
    await wait_for(lambda: _done(ada["id"], 3), msg="drew")

    from langchain_core.messages import ToolMessage

    outs = [[str(m.content) for m in p if isinstance(m, ToolMessage)][-1] for p in ap[1:]]
    assert "rectangle 'Frontend'" in outs[0]
    assert "Drew 4 shape(s)" in outs[1]
    assert "(being drawn) rectangle 'API' (api)" in outs[-1]

    scene = (await app_client.get(f"/api/v1/whiteboards/{bid}")).json()
    pend = {p.get("id"): p for p in scene["pending"]}
    assert pend["api"]["x"] >= 180  # placed to the right of the user's box
    arrow = next(p for p in scene["pending"] if p["type"] == "arrow" and p.get("label"))
    assert arrow["start"] == {"id": "u1"} and arrow["end"] == {"id": "api"}
    assert all(p["customData"] == {"by": "Ada"} for p in scene["pending"])

    # An open editor claims the queue exactly once.
    claimed = (await app_client.post(f"/api/v1/whiteboards/{bid}/pending/claim")).json()
    assert len(claimed["pending"]) == 4
    assert (await app_client.post(f"/api/v1/whiteboards/{bid}/pending/claim")).json() == \
        {"pending": []}

    # Converted elements come back tagged with who drew them; erase removes only Ada's.
    converted = [el("api", 1, x=260, customData={"by": "Ada"}),
                 {"id": "apit", "type": "text", "text": "API", "containerId": "api",
                  "version": 1, "versionNonce": 1, "isDeleted": False}]
    await app_client.put(f"/api/v1/whiteboards/{bid}/scene", json={"elements": converted})
    from app.services import whiteboards as wb

    assert await wb.erase(bid, by="Ada") == 2
    text = wb.describe(await wb.get(bid))
    assert "'Frontend'" in text and "'API'" not in text


async def test_thumbnails_round_trip(app_client):
    org = await make_org(app_client)
    bid = (await app_client.get(f"/api/v1/orgs/{org['id']}/whiteboards")).json()[0]["id"]
    assert (await app_client.get(f"/api/v1/whiteboards/{bid}/thumbnail.png")).status_code == 404
    png = "data:image/png;base64,iVBORw0KGgo="
    assert (await app_client.put(f"/api/v1/whiteboards/{bid}/thumbnail",
                                 json={"dataUrl": png})).status_code == 204
    r = await app_client.get(f"/api/v1/whiteboards/{bid}/thumbnail.png")
    assert r.status_code == 200 and r.content.startswith(b"\x89PNG")
    bad = await app_client.put(f"/api/v1/whiteboards/{bid}/thumbnail",
                               json={"dataUrl": "javascript:alert(1)"})
    assert bad.status_code == 400
