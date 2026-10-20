"""World building: agents shape their own organization's space, safely and undoably."""

from __future__ import annotations

import io
import json
import zipfile

from app.models import Turn
from app.services import assets
from tests.conftest import wait_for
from tests.helpers import call, dm, rows, say, script, tool_results, use_classic_templates

WORLD = "templates-classic/world-builders"  # Pantheon 1's living world


def tiny_glb(texture_uri: str | None = None) -> bytes:
    gltf = {"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
            "accessors": [{"min": [-0.5, 0, -0.5], "max": [0.5, 1, 0.5], "type": "VEC3",
                           "componentType": 5126, "count": 0}],
            "buffers": [{"byteLength": 0}]}
    if texture_uri:
        gltf["images"] = [{"uri": texture_uri}]
    return assets._glb_build(gltf, b"")


async def _done(agent_id: str, n: int = 1) -> bool:
    return sum(t.status == "completed" for t in await rows(Turn, Turn.agent_id == agent_id)) >= n


async def test_agents_build_their_world(app_client, monkeypatch):
    await use_classic_templates(app_client)
    org = (await app_client.post("/api/v1/orgs", json={"template": WORLD})).json()
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    assert snap["org"]["settings"]["world"]["enabled"]
    by_name = {a["name"]: a for a in snap["agents"]}
    mason = by_name["Mason"]
    assert by_name["Iris"]["avatar"]["character"] == "pet-fox"
    office = snap["org"]["layout"]["office"]
    desk = next(i for i in office["items"] if i.get("agentId") == mason["id"])

    pack = io.BytesIO()
    with zipfile.ZipFile(pack, "w") as z:
        z.writestr("kit/Models/GLB format/lantern.glb", tiny_glb("Textures/colormap.png"))
        z.writestr("kit/Models/GLB format/Textures/colormap.png", b"\x89PNG fake")

    async def fake_download(url: str) -> bytes:
        return pack.getvalue()

    monkeypatch.setattr(assets, "download", fake_download)

    def placed_id(messages) -> str:
        import re
        return next(m.group(1) for r in tool_results(messages)
                    if (m := re.search(r"\((it_[0-9a-z_]+)\)", r)))

    prompts = script(
        mason["id"],
        call("world_catalog", query="sofa"),
        call("world_place", item="loungeSofa", room="Campsite"),
        call("world_place", item="loungeSofa", x=400, z=5),  # past the open world's limit
        lambda msgs, tools: call("world_animate", id=placed_id(msgs), spin=0.2),
        call("world_make_asset", label="Beacon", parts=[
            {"shape": "cylinder", "size": [0.3, 1.2, 0.3], "pos": [0, 0.6, 0], "color": "#334155"},
            {"shape": "sphere", "size": [0.4, 0.4, 0.4], "pos": [0, 1.4, 0], "color": "#fde68a",
             "emissive": "#fbbf24"}]),
        call("world_room", name="Garden", type="lounge", x=18, z=2, w=8, d=6, walls="none", door="w"),
        call("world_resize", width=50),
        call("world_undo"),
        call("world_remove", id=desk["id"]),
        call("world_import_model", url="https://example.com/x.glb", label="X", license="CC0",
             height_m=1),
        call("world_import_model", url="https://kenney.nl/media/kit.zip", label="Lantern",
             license="CC0", height_m=0.5),
        call("world_import_model", url="https://kenney.nl/media/kit.zip", file="lantern",
             label="Lantern", license="CC0", height_m=0.5),
        call("world_make_action", name="ring_the_bell", description="Ring the plot's bell",
             narration="{actor} rings the bell in the {room}", gesture="wave", give_to=["Pip"]),
        call("world_spawn", name="bell", where="Campsite"),
        say("built"), then=say("ok"))
    await dm(app_client, org["id"], "Mason", "build something")
    await wait_for(lambda: _done(mason["id"]), timeout=60, msg="Mason finished")
    r = tool_results(prompts[-1])
    assert "loungeSofa" in r[0] or "Sofa" in r[0]
    assert r[1].startswith("Placed") and "outside the space" in r[2]
    assert "spin 0.2" in r[3]
    assert "Made asset:" in r[4] and "Garden" in r[5] and "50" in r[6]
    assert "Undid: resized the space" in r[7]
    assert "someone's desk" in r[8]
    assert "isn't one of the free model libraries" in r[9]
    assert "say which model" in r[10] and "lantern" in r[10]
    assert "Imported asset:" in r[11]
    assert "'ring_the_bell' is ready for" in r[12] and "Mason" in r[12] and "Pip" in r[12]
    assert "bell" in r[13]

    layout = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["org"]["layout"]
    design = layout["office"]
    assert design["width"] == 96  # the resize was undone
    assert any(rm["name"] == "Garden" for rm in design["rooms"])
    sofa = next(i for i in design["items"] if i["kind"] == "loungeSofa" and i.get("by") == "Mason")
    assert sofa["anim"] == {"spin": 0.2}
    assert any(i.get("agentId") == mason["id"] for i in design["items"])  # the desk stayed
    assert 1 <= len(layout["office_history"]) <= 10

    beacon = next(a for a in await assets.all_assets() if a["label"] == "Beacon")
    assert any(p.get("emissive") == "#fbbf24" for p in beacon["parts"])
    lantern = next(a for a in await assets.all_assets() if a["label"] == "Lantern")
    stored = (assets.settings.assets_path / f"{lantern['key'].split(':')[1]}.glb").read_bytes()
    gltf, binary = assets._glb_parts(stored)
    view = gltf["bufferViews"][gltf["images"][0]["bufferView"]]
    assert "uri" not in gltf["images"][0]
    assert binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]] == b"\x89PNG fake"

    pip = next(a for a in (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["agents"]
               if a["name"] == "Pip")
    assert any(t["name"] == "ring_the_bell" for t in pip["tools"])
    assert json.dumps(design)  # still a plain document
