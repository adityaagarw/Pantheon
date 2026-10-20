"""The whiteboard grid (cells, placing next to shapes, diagrams) and the picture agents see."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.models import Whiteboard
from app.services import wbgrid, wbrender
from tests.conftest import wait_for
from tests.helpers import call, dm, make_agent, make_org, rows, say, script, tool_results


def test_cells_ranges_and_names():
    assert [wbgrid.col_name(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]
    assert wbgrid.parse_cell("c4") == (2, 3) and wbgrid.col_index("AB") == 27
    assert wbgrid.parse_range("D4:B3") == (1, 2, 3, 3)
    box = wbgrid.box_in("B2")
    assert box == {"x": 220 + 20, "y": 140 + 30, "width": 180, "height": 80}
    assert wbgrid.cell_at(box["x"] + 5, box["y"] + 5) == "B2"
    big = wbgrid.box_in("B2:C3")
    assert wbgrid.range_of(big["x"], big["y"], big["width"], big["height"]) == "B2:C3"
    with pytest.raises(wbgrid.GridError):
        wbgrid.parse_cell("3B")


def test_placing_next_to_a_shape_skips_taken_cells():
    api = {"id": "api", **wbgrid.box_in("B2")}
    taken = wbgrid.occupied([(api["x"], api["y"], api["x"] + api["width"], api["y"] + api["height"])])
    taken.add((2, 1))  # C2 is already used
    db = wbgrid.place({"type": "rectangle", "near": "api", "side": "right"}, {"api": api}, taken)
    assert wbgrid.cell_at(db["x"] + 1, db["y"] + 1) == "D2"
    below = wbgrid.place({"type": "rectangle", "near": "API", "side": "below"}, {"api": api}, taken)
    assert wbgrid.cell_at(below["x"] + 1, below["y"] + 1) == "B3"
    with pytest.raises(wbgrid.GridError, match="no room"):
        wbgrid.place({"type": "rectangle", "near": "api", "side": "above"},
                     {"api": {"id": "api", **wbgrid.box_in("B1")}}, set())


def test_diagrams_land_on_the_grid():
    flow = wbgrid.diagram("flowchart", {"nodes": [{"id": "a", "label": "Start"}, {"id": "b"},
                                                  {"id": "c"}],
                                        "edges": [{"from": "a", "to": "b"}, {"from": "a", "to": "c"}],
                                        "at": "A1"}, set())
    boxes = {s["id"]: wbgrid.cell_at(s["x"] + 1, s["y"] + 1) for s in flow if s["type"] == "rectangle"}
    assert boxes["a"] != boxes["b"] and boxes["b"][0] == boxes["c"][0] == "B"  # one layer per step
    assert sum(s["type"] == "arrow" for s in flow) == 2
    down = wbgrid.diagram("flowchart", {"nodes": [{"id": "a"}, {"id": "b"}],
                                        "edges": [{"from": "a", "to": "b"}], "direction": "down"}, set())
    assert down[0]["x"] == down[1]["x"] and down[1]["y"] > down[0]["y"]

    mind = wbgrid.diagram("mindmap", {"center": "Product", "branches": [
        {"label": "Users", "children": ["Teams", "Solo"]}, {"label": "Price"}]}, set())
    centre = next(s for s in mind if s["id"] == "mm_center")
    users = next(s for s in mind if s.get("label") == "Users")
    price = next(s for s in mind if s.get("label") == "Price")
    assert users["x"] > centre["x"] > price["x"]  # branches on both sides
    assert sum(s["type"] == "line" for s in mind) == 4

    table = wbgrid.diagram("table", {"columns": ["Name", "Role"], "rows": [["Ada", "Dev"]]}, {(0, 0)})
    assert len(table) == 4 and table[0]["fill"] and table[0]["x"] >= 2 * wbgrid.CELL_W
    assert len(wbgrid.diagram("timeline", {"events": [{"when": "Q1", "label": "Plan"},
                                                      {"when": "Q2", "label": "Ship"}]}, set())) == 5
    assert "• one" in wbgrid.diagram("list", {"title": "To do", "items": ["one"]}, set())[0]["text"]


def test_the_board_renders_with_its_grid():
    live = [{"id": "r", "type": "rectangle", "x": 240, "y": 170, "width": 180, "height": 80,
             "strokeColor": "#1971c2", "backgroundColor": "#a5d8ff"},
            {"id": "t", "type": "text", "text": "API", "containerId": "r", "fontSize": 20, "x": 0, "y": 0},
            {"id": "a", "type": "arrow", "x": 420, "y": 210, "points": [[0, 0], [200, 0]],
             "strokeColor": "#1e1e1e"}]
    pending = [{"id": "p", "type": "ellipse", "x": 680, "y": 170, "width": 160, "height": 90,
                "label": {"text": "Queue"}}]
    png = wbrender.render(live, pending)
    img = Image.open(io.BytesIO(png))
    assert img.format == "PNG" and img.width > 600 and img.height > 300
    # The box's fill is there, inside the gutter-shifted cell B2.
    assert img.getpixel((wbrender.GUTTER + 330, wbrender.GUTTER + 190)) != (255, 255, 255)
    assert wbrender.data_url(png).startswith("data:image/png;base64,")


async def test_agents_draw_on_cells_and_see_the_board(app_client):
    org = await make_org(app_client)
    ada = await make_agent(app_client, org["id"], "Ada")
    tools = [{"name": n, "approval": "auto"} for n in
             ("whiteboard_draw", "whiteboard_view", "whiteboard_diagram", "whiteboard_read")]
    await app_client.patch(f"/api/v1/agents/{ada['id']}", json={"tools": tools})
    prompts = script(
        ada["id"],
        call("whiteboard_draw", shapes=[{"type": "rectangle", "id": "api", "label": "API", "cell": "B2"},
                                        {"type": "rectangle", "id": "db", "label": "DB",
                                         "near": "api", "side": "right"},
                                        {"type": "arrow", "from": "api", "to": "db"}]),
        call("whiteboard_diagram", kind="flowchart", nodes=[{"id": "x", "label": "Idea"},
                                                            {"id": "y", "label": "Build"}],
             edges=[{"from": "x", "to": "y"}]),
        call("whiteboard_draw", shapes=[{"type": "rectangle", "cell": "9Z"}]),
        call("whiteboard_view"),
        say("drawn"), then=say("ok"))
    await dm(app_client, org["id"], "Ada", "sketch the system")
    await wait_for(lambda: _idle_after(ada["id"], prompts, 5), timeout=60, msg="Ada drew")
    r = tool_results(prompts[-1])
    assert r[0].startswith("Drew 3 shape(s)")
    assert "Drew a flowchart" in r[1] and " in E1" in r[1]  # one empty column after the rest
    assert "not a cell" in r[2]
    assert "picture attached" in r[3] and "'API' in B2" in r[3] and "'DB' in C2" in r[3]
    board = (await rows(Whiteboard, Whiteboard.org_id == org["id"]))[0]
    by_id = {p["id"]: p for p in board.pending}
    assert wbgrid.cell_at(by_id["db"]["x"] + 1, by_id["db"]["y"] + 1) == "C2"


async def _idle_after(agent_id: str, prompts: list, n: int) -> bool:
    from app.models import Agent

    a = (await rows(Agent, Agent.id == agent_id))[0]
    return len(prompts) >= n and a.runtime_status == "idle"
