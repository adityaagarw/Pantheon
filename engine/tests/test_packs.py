# ruff: noqa: E501  (the fake web responses are minified modules)
"""Packs: Stage libraries chosen per org and agent, switching packs off, adding and removing
them, and npm libraries fetched on demand."""

from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest

from app import packs
from app.models import Agent, ToolCall
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script


@pytest.fixture
def user_packs(tmp_path, monkeypatch):
    """The user's pack folder, empty, for this test only."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "packs_dir", str(tmp_path / "packs"))
    packs.load_all(force=True)
    yield tmp_path / "packs"
    monkeypatch.undo()
    packs.load_all(force=True)


async def _done(agent_id: str, n: int) -> bool:
    calls = await rows(ToolCall, ToolCall.agent_id == agent_id)
    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(calls) >= n and a.runtime_status == "idle"


async def _page_view(client, org_id: str, agent: dict, code: str) -> str:
    """The import map of a page the agent shows."""
    script(agent["id"], call("stage_show", title=f"By {agent['name']}", code=code), say("ok"))
    await dm(client, org_id, agent["name"], "Show it.")
    await wait_for(lambda: _done(agent["id"], 1), msg="shown")
    pages = (await client.get(f"/api/v1/orgs/{org_id}/stage")).json()
    page = next(p for p in pages if p["agentId"] == agent["id"])
    body = (await client.get(f"/api/v1/stage/{page['id']}/view")).text
    return json.loads(body.split('<script type="importmap">', 1)[1].split("</script>", 1)[0])["imports"]


async def test_the_shipped_libraries_are_packs(app_client):
    listing = (await app_client.get("/api/v1/packs")).json()
    by_id = {p["id"]: p for p in listing["packs"]}
    assert {"three", "katex", "gsap", "pixi", "d3", "rough", "postprocessing", "troika",
            "town-square"} <= set(by_id)
    assert by_id["three"]["core"] and by_id["three"]["bundled"] and not by_id["gsap"]["core"]
    assert by_id["gsap"]["kinds"] == ["library"] and by_id["gsap"]["enabled"]
    libs = {lib["id"]: lib for lib in listing["libraries"]}
    assert libs["postprocessing"]["requires"] == ["three"]
    assert "pixi-filters" in libs["pixi"]["imports"]
    docs = (await app_client.get("/api/v1/packs/libraries/gsap/docs")).json()["docs"]
    assert "MorphSVG" in docs
    # Library files are served where Pantheon 1 served them; the Stage's own files too.
    for path in ("gsap/gsap.js", "three/addons/controls/OrbitControls.js", "katex/katex.min.css",
                 "stage.js", "art.js", "fonts/fonts.css", "icons/icons-data.js"):
        assert (await app_client.get(f"/api/v1/stage-lib/{path}")).status_code == 200, path
    assert (await app_client.get("/api/v1/stage-lib/gsap/pack.json")).status_code == 200
    assert (await app_client.get("/api/v1/stage-lib/nope/x.js")).status_code == 404


async def test_orgs_and_agents_choose_their_libraries(app_client):
    org = await make_org(app_client)
    tools = [{"name": n} for n in ("stage_show", "stage_docs")]
    ada = await make_agent(app_client, org["id"], "Ada", tools=tools)
    ben = await make_agent(app_client, org["id"], "Ben", tools=tools)

    # By default every library is there.
    imports = await _page_view(app_client, org["id"], ada, "// ALL")
    assert {"three", "katex", "stage", "gsap", "d3", "pixi.js", "roughjs"} <= set(imports)
    assert imports["gsap"] == "/api/v1/stage-lib/gsap/gsap.js"

    # The org narrows it; what a library needs and the core come along.
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}",
                               json={"settings": {"libraries": ["postprocessing"]}})
    assert r.status_code == 200 and r.json()["settings"]["libraries"] == ["postprocessing"]
    imports = await _page_view(app_client, org["id"], ben, "// ORG")
    assert {"three", "katex", "postprocessing"} <= set(imports)
    assert "gsap" not in imports and "d3" not in imports

    # An agent's own choice wins; null goes back to the org's.
    r = await app_client.patch(f"/api/v1/agents/{ada['id']}", json={"libraries": ["d3"]})
    assert r.json()["libraries"] == ["d3"]
    script(ada["id"], call("stage_docs"), say("ok"))
    await dm(app_client, org["id"], "Ada", "Read the docs.")
    await wait_for(lambda: _done(ada["id"], 2), msg="docs read")
    docs = (await rows(ToolCall, ToolCall.agent_id == ada["id"], ToolCall.name == "stage_docs"))[0]
    assert '## D3 ("d3")' in docs.result and "MorphSVG" not in docs.result
    pages = (await app_client.get(f"/api/v1/orgs/{org['id']}/stage")).json()
    page = next(p for p in pages if p["agentId"] == ada["id"])
    body = (await app_client.get(f"/api/v1/stage/{page['id']}/view")).text
    assert '"d3": "/api/v1/stage-lib/d3/d3.js"' in body and '"gsap"' not in body
    r = await app_client.patch(f"/api/v1/agents/{ada['id']}", json={"libraries": None})
    assert r.json()["libraries"] is None

    # Every library again; a library id that isn't a list is refused.
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"libraries": None}})
    assert "libraries" not in r.json()["settings"]
    r = await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"libraries": "d3"}})
    assert r.status_code == 400


async def test_packs_switch_off_except_the_stages_own(app_client):
    r = await app_client.patch("/api/v1/packs/gsap", json={"enabled": False})
    assert r.status_code == 200 and not r.json()["enabled"]
    listing = (await app_client.get("/api/v1/packs")).json()
    assert "gsap" not in {lib["id"] for lib in listing["libraries"]}
    assert (await app_client.get("/api/v1/stage-lib/gsap/gsap.js")).status_code == 404
    # Remembered (the setting is in the database).
    await packs.load_state()
    assert not packs.get("gsap").enabled

    r = await app_client.patch("/api/v1/packs/three", json={"enabled": False})
    assert r.status_code == 400 and "part of the Stage" in r.json()["detail"]
    assert (await app_client.delete("/api/v1/packs/gsap")).status_code == 400  # shipped
    r = await app_client.patch("/api/v1/packs/gsap", json={"enabled": True})
    assert r.json()["enabled"]


async def test_a_plugin_packs_tools_go_when_it_is_switched_off(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[{"name": "radio_call"}])
    from app.tools.resolve import resolve_tools

    async def resolved():
        from app.core.db import SessionLocal
        from app.models import Org

        async with SessionLocal() as s:
            o, a = await s.get(Org, org["id"]), await s.get(Agent, ada["id"])
        return await resolve_tools(o, a)

    assert "radio_call" in (await resolved()).tools
    await app_client.patch("/api/v1/packs/town-square", json={"enabled": False})
    off = await resolved()
    assert "radio_call" not in off.tools and "switched off" in off.warnings[0]
    tpl = (await app_client.get("/api/v1/templates")).json()
    assert not any(t["key"].startswith("town-square/") for t in tpl)
    await app_client.patch("/api/v1/packs/town-square", json={"enabled": True})


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


async def test_the_user_adds_and_removes_packs(app_client, user_packs):
    manifest = {"id": "hello", "name": "Hello", "version": "1.0.0",
                "libraries": [{"id": "hello", "name": "Hello", "imports": {"hello": "hello.js"},
                               "docs": "docs.md"}]}
    data = _zip({"hello-pack/pack.json": json.dumps(manifest),
                 "hello-pack/hello.js": "export const hi = 1;",
                 "hello-pack/docs.md": "import { hi } from \"hello\""})
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("hello.zip", data)})
    assert r.status_code == 200, r.text
    assert r.json()["kinds"] == ["library"] and not r.json()["bundled"]
    assert (user_packs / "hello" / "hello.js").is_file()
    assert (await app_client.get("/api/v1/stage-lib/hello/hello.js")).text == "export const hi = 1;"

    bad = _zip({"pack.json": json.dumps({"id": "Bad Name"})})
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("bad.zip", bad)})
    assert r.status_code == 400
    evil = _zip({"../escape/pack.json": "{}"})
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("evil.zip", evil)})
    assert r.status_code == 400 and "unsafe" in r.json()["detail"]

    # A user's pack with a shipped pack's id replaces it, and removing it brings it back.
    d3 = {"id": "d3", "name": "D3 (newer)", "version": "8.0.0",
          "libraries": [{"id": "d3", "imports": {"d3": "d3.js"}}]}
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("d3.zip", _zip({
        "pack.json": json.dumps(d3), "d3.js": "export const v = 8;"}))})
    assert r.json()["version"] == "8.0.0"
    assert (await app_client.get("/api/v1/stage-lib/d3/d3.js")).text == "export const v = 8;"
    assert (await app_client.delete("/api/v1/packs/d3")).status_code == 204
    assert packs.get("d3").bundled

    assert (await app_client.delete("/api/v1/packs/hello")).status_code == 204
    assert packs.get("hello") is None and not (user_packs / "hello").exists()


async def test_a_packaged_site_carries_only_the_libraries_its_pages_use(
        app_client, workspace_root, tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    org = await make_org(app_client)
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={"settings": {"libraries": ["rough"]}})
    ada = await make_agent(app_client, org["id"], "Ada", tools=[
        {"name": n} for n in ("stage_show", "stage_package")])
    script(ada["id"], call("stage_show", title="Home", code="// HOME"),
           call("stage_package", name="Site"), say("ok"))
    await dm(app_client, org["id"], "Ada", "Package it.")
    await wait_for(lambda: _done(ada["id"], 2), msg="packaged")
    site = tmp_path / "sites" / org["id"] / "site"
    assert (site / "lib" / "stage.js").is_file() and (site / "lib" / "rough" / "rough.js").is_file()
    assert (site / "lib" / "three" / "three.module.js").is_file()
    assert not (site / "lib" / "gsap").exists() and not (site / "lib" / "rough" / "pack.json").exists()
    assert '"roughjs": "./lib/rough/rough.js"' in (site / "index.html").read_text(encoding="utf-8")


# --- npm libraries on demand ---------------------------------------------------------------

THREE_ADDON = "examples/jsm/geometries/TextGeometry.js"


def _fake_web(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://registry.npmjs.org/globe-kit/latest":
        return httpx.Response(200, json={
            "name": "globe-kit", "version": "1.2.3", "description": "Globes in three.js.",
            "license": "MIT", "peerDependencies": {"three": ">=0.150"}})
    if url.startswith("https://esm.sh/globe-kit@1.2.3?"):
        assert "external=three" in url
        return httpx.Response(200, text='/* esm.sh */\nexport * from "/globe-kit@1.2.3/es2022/globe-kit.mjs";')
    if url == "https://esm.sh/globe-kit@1.2.3/es2022/globe-kit.mjs":
        return httpx.Response(200, text=(
            'import{Mesh as M}from"three";import{TextGeometry}from"three/examples/jsm/geometries/TextGeometry.js";'
            'import{ease}from"./util.mjs";export const globe=()=>new M();export{ease};'))
    if url == "https://esm.sh/globe-kit@1.2.3/es2022/util.mjs":
        return httpx.Response(200, text="export const ease=t=>t;")
    if url == f"https://cdn.jsdelivr.net/npm/three@0.186.0/{THREE_ADDON}":
        return httpx.Response(200, text='import{ExtrudeGeometry}from"three";export class TextGeometry extends ExtrudeGeometry{}')
    if url == "https://cdn.jsdelivr.net/npm/globe-kit@1.2.3/README.md":
        return httpx.Response(200, text="# globe-kit\n[![build](x.svg)](y)\n\nglobe() makes a globe.")
    return httpx.Response(404)


async def test_any_npm_library_becomes_a_pack(app_client, user_packs, monkeypatch):
    from app.services import npmpack

    real = httpx.AsyncClient
    monkeypatch.setattr(npmpack.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(_fake_web), **kw))
    r = await app_client.post("/api/v1/packs/npm", json={"spec": "globe-kit"})
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["id"] == "globe-kit" and info["version"] == "1.2.3" and info["warnings"] == []
    lib = info["libraries"][0]
    assert lib["requires"] == ["three"]
    assert "three/examples/jsm/geometries/TextGeometry.js" in lib["imports"]

    root = user_packs / "globe-kit"
    entry = (root / "index.js").read_text(encoding="utf-8")
    assert 'from "./mods/esm.sh/globe-kit@1.2.3/es2022/globe-kit.mjs"' in entry
    mod = (root / "mods/esm.sh/globe-kit@1.2.3/es2022/globe-kit.mjs").read_text(encoding="utf-8")
    assert 'from"three"' in mod and 'from"./util.mjs"' in mod  # shared three, local util
    assert (root / "three-addons/geometries/TextGeometry.js").is_file()
    docs = (await app_client.get("/api/v1/packs/libraries/globe-kit/docs")).json()["docs"]
    assert "globe() makes a globe." in docs and "[![build]" not in docs

    # A page that may use it gets it, and three.js still comes from the three pack.
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada", tools=[{"name": "stage_show"}],
                           libraries=["globe-kit"])
    imports = await _page_view(app_client, org["id"], ada, "// GLOBE")
    assert imports["globe-kit"] == "/api/v1/stage-lib/globe-kit/index.js"
    assert imports["three"] == "/api/v1/stage-lib/three/three.module.js"
    assert imports["three/examples/jsm/geometries/TextGeometry.js"] == \
        "/api/v1/stage-lib/globe-kit/three-addons/geometries/TextGeometry.js"
    assert "gsap" not in imports

    r = await app_client.post("/api/v1/packs/npm", json={"spec": "not a package!"})
    assert r.status_code == 400


# --- the office ------------------------------------------------------------------------------


async def test_the_original_office_is_a_pack(app_client):
    listing = (await app_client.get("/api/v1/packs")).json()
    classic = next(p for p in listing["packs"] if p["id"] == "office-classic")
    assert classic["core"] and classic["kinds"] == ["furniture", "characters", "themes"]
    assert classic["office"] == {"items": 540, "characters": 57, "themes": 7}
    r = await app_client.patch("/api/v1/packs/office-classic", json={"enabled": False})
    assert r.status_code == 400

    office = (await app_client.get("/api/v1/office")).json()["packs"]
    assert [p["id"] for p in office] == ["office-classic"]
    o = office[0]
    assert o["base"] == "/api/v1/packs/office-classic/files"
    assert {"workstation", "desk", "factory:machine"} <= {i["key"] for i in o["items"]}
    assert o["kits"]["nature"] == {"scale": 3.5} and "furniture/desk" in o["models"]
    model = await app_client.get(o["base"] + "/models/furniture/desk.glb")
    assert model.status_code == 200 and model.headers["content-type"] == "model/gltf-binary"
    female = next(c for c in o["characters"] if c["key"] == "female-a")
    assert (await app_client.get(f"{o['base']}/{female['thumb']}")).status_code == 200

    # World building and themes read the packs.
    from app.services import worldbuild

    assert worldbuild.catalog()["workstation"]["workstation"] is True
    assert packs.theme_ids()[0] == "classic" and "station" in packs.theme_ids()


async def test_a_pack_adds_furniture_characters_and_a_theme(app_client, user_packs):
    from app.services import worldbuild

    office = {
        "kits": {"neon": {"scale": 1}},
        "models": {"neon/sign": {"min": [-0.5, 0, -0.1], "max": [0.5, 1, 0.1]}},
        "items": [{"key": "neon:sign", "label": "Neon sign", "category": "Neon", "size": [1, 0.2],
                   "parts": [{"model": "neon/sign", "x": 0, "z": 0}], "blocking": False}],
        "rigs": {"bot": {"fit": 1.2, "sitLift": 0.3, "clips": {"idle": "Idle", "walk": "Walk",
                 "sit": "Sit", "wave": "Wave", "yes": "Yes", "no": "No", "thumbs": "Yes"}}},
        "characters": [{"key": "neon:bot", "label": "Neon bot", "group": "Neon",
                        "model": "models/bot.glb", "rig": "bot"}],
        "themes": [{"id": "neon", "label": "Neon nights", "floor": "slate", "sky": "#05010d"}],
    }
    files = {"pack.json": json.dumps({"id": "neon", "name": "Neon", "office": "office.json"}),
             "office.json": json.dumps(office), "models/neon/sign.glb": "glb", "models/bot.glb": "glb"}
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("neon.zip", _zip(files))})
    assert r.status_code == 200, r.text
    assert r.json()["kinds"] == ["furniture", "characters", "themes"]
    ids = [p["id"] for p in (await app_client.get("/api/v1/office")).json()["packs"]]
    assert ids == ["office-classic", "neon"]  # shipped first: a user's pack wins a shared key
    assert worldbuild.catalog()["neon:sign"] == {"label": "Neon sign", "category": "Neon",
                                                 "size": [1.0, 0.2], "blocking": False,
                                                 "seats": 0, "workstation": False}
    assert "neon" in packs.theme_ids()

    # Switched off, it's gone from the office and world building.
    await app_client.patch("/api/v1/packs/neon", json={"enabled": False})
    assert "neon:sign" not in worldbuild.catalog() and "neon" not in packs.theme_ids()

    # A broken pack says what's wrong.
    bad = {**office, "items": [{**office["items"][0], "parts": [{"model": "neon/missing", "x": 0, "z": 0}]}]}
    files["office.json"] = json.dumps(bad)
    files["pack.json"] = json.dumps({"id": "neon2", "office": "office.json"})
    r = await app_client.post("/api/v1/packs/upload", files={"file": ("bad.zip", _zip(files))})
    assert "neon/missing" in r.json()["error"]
