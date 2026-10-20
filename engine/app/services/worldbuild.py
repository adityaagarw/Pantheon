"""World building: agents change the space they live in, inside their own organization.

Everything happens on the organization's saved office design (``org.layout.office``,
the same document the Office designer edits), so the user sees changes live and can
keep editing by hand. Every change keeps the previous design in a short history, so
any of it can be undone. What may be placed comes from the office packs' furniture
(app/packs.py) and the 3D assets.
"""

from __future__ import annotations

import copy
import math
from typing import Any

from app import packs
from app.core.ids import new_id
from app.models import Org
from app.services import assets, orgs

HISTORY = 10
MAX_ITEMS = 2500
MAX_ROOMS = 120
MAX_SIDE = 300.0  # meters
ROOM_TYPES = ("meeting", "lounge", "kitchen", "team", "focus", "reception", "home", "bedroom",
              "washroom", "garden", "custom")
FLOORS = ("oak", "walnut", "concrete", "polished", "slate", "epoxy", "carpet", "tile", "grass",
          "asphalt")
ANIMS = ("spin", "bob", "pulse", "swing")


class BuildError(ValueError):
    pass


def catalog() -> dict[str, dict[str, Any]]:
    return packs.office_items()


def design_of(org: Org) -> dict[str, Any]:
    d = (org.layout or {}).get("office")
    if not (isinstance(d, dict) and isinstance(d.get("items"), list) and isinstance(d.get("rooms"), list)):
        raise BuildError("this organization's office isn't saved yet, so it can't be built on: "
                         "ask the user to open the Office designer and press Save once")
    return d


async def kind_info(kind: str) -> tuple[str, dict[str, Any]]:
    """A placeable thing: a catalog key, or a 3D asset by key or label."""
    kind = kind.strip()
    if kind in catalog():
        return kind, catalog()[kind]
    found = await assets.resolve_key(kind)
    if found:
        w, d = (found.get("size") or [1, 1, 1])[:2]
        return found["key"], {"label": found["label"], "category": found.get("category", "Props"),
                              "size": [w, d], "blocking": not found.get("carryable"),
                              "seats": 0, "workstation": False}
    raise BuildError(f"no furniture or asset called '{kind}' (search with world_catalog)")


def matches(query: str, text: str) -> int:
    """How many of the query's words appear in the text (plurals count: "trees" finds tree)."""
    text = text.lower()
    return sum(1 for w in query.lower().split() if w in text or w.rstrip("s") in text and len(w) > 3)


def search(query: str, limit: int = 40) -> list[tuple[str, dict[str, Any]]]:
    """Best matches first: things matching every word, then most words."""
    scored = [(n, k, e) for k, e in catalog().items()
              if (n := matches(query, f"{k} {e['label']} {e['category']}"))]
    scored.sort(key=lambda t: -t[0])
    return [(k, e) for _, k, e in scored[:limit]]


def footprint(info: dict[str, Any], rot: float) -> tuple[float, float]:
    w, d = info["size"]
    quarter = round(rot / (math.pi / 2)) % 2
    return (d, w) if quarter else (w, d)


def bounds(it: dict[str, Any], info: dict[str, Any]) -> tuple[float, float, float, float]:
    w, d = footprint(info, float(it.get("rot") or 0))
    return float(it["x"]) - w / 2, float(it["z"]) - d / 2, w, d


def _overlap(a: tuple, b: tuple) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


async def _blockers(design: dict[str, Any], skip: str | None = None) -> list[tuple]:
    out = []
    for it in design["items"]:
        if it.get("id") == skip or it.get("y"):
            continue
        try:
            _, info = await kind_info(str(it.get("kind", "")))
        except BuildError:
            continue
        if info.get("blocking", True):
            out.append(bounds(it, info))
    return out


def room_named(design: dict[str, Any], name: str) -> dict[str, Any]:
    for r in design["rooms"]:
        if str(r.get("name", "")).lower() == name.strip().lower():
            return r
    names = ", ".join(str(r.get("name")) for r in design["rooms"]) or "none yet"
    raise BuildError(f"no room called '{name}' (rooms: {names})")


async def free_spot(design: dict[str, Any], info: dict[str, Any], rot: float,
                    area: tuple[float, float, float, float]) -> tuple[float, float]:
    w, d = footprint(info, rot)
    blockers = await _blockers(design)
    x0, z0, aw, ad = area
    z = z0 + d / 2 + 0.4
    while z + d / 2 <= z0 + ad - 0.2:
        x = x0 + w / 2 + 0.4
        while x + w / 2 <= x0 + aw - 0.2:
            box = (x - w / 2 - 0.3, z - d / 2 - 0.3, w + 0.6, d + 0.6)
            if not any(_overlap(box, b) for b in blockers):
                return round(x, 2), round(z, 2)
            x += 0.35
        z += 0.35
    raise BuildError("there's no free space for that there; make room or pick another spot")


async def save(org: Org, design: dict[str, Any], by: str, what: str) -> None:
    """Store the new design, keeping the previous one for undo."""
    layout = dict(org.layout or {})
    history = list(layout.get("office_history") or [])
    if isinstance(layout.get("office"), dict):
        history.append({"design": layout["office"], "by": by, "what": what})
    layout["office_history"] = history[-HISTORY:]
    layout["office"] = design
    await orgs.update_org(org.id, {"layout": layout})
    from app.services import life

    await life.nudge(org.id, by, {"fun": 3})  # making things is a little fun, in a living world


async def undo(org: Org) -> str:
    layout = dict(org.layout or {})
    history = list(layout.get("office_history") or [])
    if not history:
        raise BuildError("nothing to undo")
    last = history.pop()
    layout["office"], layout["office_history"] = last["design"], history
    await orgs.update_org(org.id, {"layout": layout})
    return f"undid: {last.get('what')} (by {last.get('by')})"


def fit(design: dict[str, Any], x0: float, z0: float, x1: float, z1: float) -> None:
    """Check a rectangle is inside the space. An open world grows east and south to fit."""
    if x0 < 0 or z0 < 0:
        raise BuildError(f"({x0:.1f}, {z0:.1f}) is outside the space: x and z start at 0 "
                         "(the north-west corner)")
    if design.get("open") and x1 <= MAX_SIDE and z1 <= MAX_SIDE:
        design["width"] = max(float(design["width"]), math.ceil(x1 + 4))
        design["depth"] = max(float(design["depth"]), math.ceil(z1 + 4))
        design["width"], design["depth"] = min(design["width"], MAX_SIDE), min(design["depth"], MAX_SIDE)
        return
    if x1 > float(design["width"]) or z1 > float(design["depth"]):
        raise BuildError(f"({x1:.1f}, {z1:.1f}) is outside the space, which is {design['width']} x "
                         f"{design['depth']} m (world_resize makes it bigger, up to {MAX_SIDE:.0f} m)")


def check_inside(design: dict[str, Any], x: float, z: float, info: dict[str, Any], rot: float) -> None:
    w, d = footprint(info, rot)
    fit(design, x - w / 2, z - d / 2, x + w / 2, z + d / 2)


async def place(org: Org, kind: str, *, x: float | None, z: float | None, rot_deg: float,
                room: str | None, y: float | None, by: str) -> dict[str, Any]:
    design = copy.deepcopy(design_of(org))
    if len(design["items"]) >= MAX_ITEMS:
        raise BuildError(f"the space already has {MAX_ITEMS} things; remove some first")
    key, info = await kind_info(kind)
    rot = math.radians(rot_deg)
    if x is None or z is None:
        area = (0.0, 0.0, float(design["width"]), float(design["depth"]))
        if room:
            r = room_named(design, room)
            area = (float(r["x"]), float(r["z"]), float(r["w"]), float(r["d"]))
        x, z = await free_spot(design, info, rot, area)
    check_inside(design, x, z, info, rot)
    item = {"id": new_id("it"), "kind": key, "x": round(x, 2), "z": round(z, 2),
            "rot": round(rot, 4), "by": by}
    if y:
        item["y"] = round(max(0.0, min(float(y), 20.0)), 2)
    design["items"].append(item)
    await save(org, design, by, f"placed {info['label']}")
    return {**item, "label": info["label"]}


def find_item(design: dict[str, Any], ref: str) -> dict[str, Any]:
    for it in design["items"]:
        if it.get("id") == ref:
            return it
    raise BuildError(f"no item '{ref}' (world_plan lists them with their ids)")


async def move(org: Org, ref: str, *, x: float | None, z: float | None, rot_deg: float | None,
               y: float | None, by: str) -> dict[str, Any]:
    design = copy.deepcopy(design_of(org))
    it = find_item(design, ref)
    _, info = await kind_info(str(it["kind"]))
    nx = float(it["x"]) if x is None else x
    nz = float(it["z"]) if z is None else z
    rot = float(it.get("rot") or 0) if rot_deg is None else math.radians(rot_deg)
    check_inside(design, nx, nz, info, rot)
    it.update(x=round(nx, 2), z=round(nz, 2), rot=round(rot, 4))
    if y is not None:
        it["y"] = round(max(0.0, min(float(y), 20.0)), 2)
    await save(org, design, by, f"moved {info['label']}")
    return it


async def remove(org: Org, ref: str, by: str) -> str:
    design = copy.deepcopy(design_of(org))
    it = find_item(design, ref)
    _, info = await kind_info(str(it["kind"]))
    if info.get("workstation") and it.get("agentId"):
        raise BuildError("that's someone's desk; it stays")
    design["items"] = [i for i in design["items"] if i is not it]
    await save(org, design, by, f"removed {info['label']}")
    return info["label"]


async def animate(org: Org, ref: str, anim: dict[str, Any], by: str) -> dict[str, Any]:
    design = copy.deepcopy(design_of(org))
    it = find_item(design, ref)
    clean: dict[str, Any] = {}
    for k in ANIMS:
        if anim.get(k):
            clean[k] = round(max(-10.0, min(float(anim[k]), 10.0)), 3)
    if anim.get("clip"):
        clean["clip"] = str(anim["clip"])[:80]
    if clean:
        it["anim"] = clean
    else:
        it.pop("anim", None)
    await save(org, design, by, "animated an item" if clean else "stopped an animation")
    return it


async def set_room(org: Org, name: str, *, type_: str, x: float, z: float, w: float, d: float,
                   walls: str, door: str, by: str) -> dict[str, Any]:
    design = copy.deepcopy(design_of(org))
    if type_ not in ROOM_TYPES:
        raise BuildError(f"type must be one of {', '.join(ROOM_TYPES)}")
    if walls not in ("none", "glass", "solid") or door not in ("n", "s", "e", "w"):
        raise BuildError("walls: none, glass or solid; door: n, s, e or w")
    if w < 2 or d < 2:
        raise BuildError("a room must be at least 2 x 2 m")
    fit(design, x, z, x + w, z + d)
    existing = next((r for r in design["rooms"] if str(r.get("name", "")).lower() == name.lower()), None)
    if existing is None and len(design["rooms"]) >= MAX_ROOMS:
        raise BuildError(f"at most {MAX_ROOMS} rooms")
    room = existing or {"id": new_id("rm")}
    room.update(name=name.strip()[:40], type=type_, x=round(x, 2), z=round(z, 2), w=round(w, 2),
                d=round(d, 2), walls=walls, door=door)
    if existing is None:
        design["rooms"].append(room)
    await save(org, design, by, f"{'changed' if existing else 'added'} the room {room['name']}")
    return room


async def resize(org: Org, *, width: float | None, depth: float | None, floor: str | None,
                 by: str) -> dict[str, Any]:
    design = copy.deepcopy(design_of(org))
    if width is not None:
        design["width"] = round(max(10.0, min(float(width), MAX_SIDE)), 2)
    if depth is not None:
        design["depth"] = round(max(10.0, min(float(depth), MAX_SIDE)), 2)
    if floor:
        if floor not in FLOORS:
            raise BuildError(f"floor must be one of {', '.join(FLOORS)}")
        design["floor"] = floor
    await save(org, design, by, "resized the space")
    return design


async def describe(org: Org) -> str:
    design = design_of(org)
    lines = [f"Space: {design['width']} x {design['depth']} m (x to the east, z to the south; "
             f"0, 0 is the north-west corner), floor {design.get('floor')}, theme "
             f"{design.get('theme') or 'classic'}."
             + (f" It's an open world: it grows east and south as you build past the edge "
                f"(up to {MAX_SIDE:.0f} m)." if design.get("open") else ""), "", "Rooms:"]
    for r in design["rooms"]:
        lines.append(f"- {r['name']} ({r['type']}, {r.get('walls')} walls, door {r.get('door')}): "
                     f"x {r['x']}..{round(r['x'] + r['w'], 2)}, z {r['z']}..{round(r['z'] + r['d'], 2)}")
    lines += ["", f"Things ({len(design['items'])}):"]
    for it in design["items"][:300]:
        try:
            _, info = await kind_info(str(it["kind"]))
            label = info["label"]
        except BuildError:
            label = it["kind"]
        extra = []
        if it.get("anim"):
            extra.append("animated " + ", ".join(f"{k} {v}" for k, v in it["anim"].items()))
        if it.get("by"):
            extra.append(f"by {it['by']}")
        if it.get("agentId"):
            extra.append("someone's desk")
        lines.append(f"- {it['id']} {label} ({it['kind']}) at {it['x']}, {it['z']}"
                     f"{', height ' + str(it['y']) if it.get('y') else ''}, rot "
                     f"{round(math.degrees(float(it.get('rot') or 0)))}°"
                     + (f" [{'; '.join(extra)}]" if extra else ""))
    return "\n".join(lines)
