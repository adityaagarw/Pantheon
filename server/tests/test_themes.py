"""Environment themes: templates set them, exports keep them, Zeus can change them."""

from __future__ import annotations

from app.tools import base


async def test_template_theme_export_and_zeus(app_client):
    r = await app_client.post("/api/v1/orgs", json={"template": "it-operations-center"})
    assert r.status_code == 201, r.text
    org = r.json()
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    layout = snap["org"]["layout"]
    assert layout["theme"] == "operations"
    # The themed floor plan (generated in the browser) brings its own dashboards and racks.
    assert "office" not in layout and not layout.get("extras")

    exported = (await app_client.get(f"/api/v1/orgs/{org['id']}/export")).json()
    assert exported["theme"] == "operations"

    set_theme = base.builtin("set_space_theme").handler
    out = await set_theme({"org": org["id"], "theme": "corporate"}, None)
    assert "corporate" in out
    snap = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()
    assert snap["org"]["layout"]["theme"] == "corporate"

    # A saved office keeps its layout on a theme change, unless Zeus rebuilds it.
    office = {"version": 1, "width": 20, "depth": 20, "floor": "oak", "items": [], "rooms": []}
    await app_client.patch(f"/api/v1/orgs/{org['id']}", json={
        "layout": {**snap["org"]["layout"], "office": office}})
    await set_theme({"org": org["id"], "theme": "lab"}, None)
    layout = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["org"]["layout"]
    assert layout["office"]["theme"] == "lab" and layout["office"]["width"] == 20
    out = await set_theme({"org": org["id"], "theme": "station", "rebuild": True}, None)
    assert "rebuilt" in out
    layout = (await app_client.get(f"/api/v1/orgs/{org['id']}")).json()["org"]["layout"]
    assert layout["theme"] == "station" and "office" not in layout
