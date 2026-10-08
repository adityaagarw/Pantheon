"""World-building tools: agents shape the space they live in.

Any agent can be granted these. They only ever change the agent's own organization:
its saved office design (furniture, assets, rooms, the size of the space and its
floor), animations, movable objects and actions. Every layout change can be undone.
Models come from free libraries only (CC0 / CC-BY, from known hosts) or from the
organization's Files, e.g. something made in Blender.
"""

from __future__ import annotations

import math
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, CustomTool, Org, WorldObject
from app.services import assets, customtools, orgs, spatial
from app.services import attachments as att
from app.services import worldbuild as wb
from app.services.worldbuild import BuildError
from app.tools.base import B, S, ToolContext, ToolError, arr, obj, tool
from app.tools.world import GESTURES, object_dict

NUM = {"type": "number"}
# Hosts of free 3D model libraries an agent may import from without asking.
FREE_HOSTS = ("kenney.nl", "poly.pizza", "static.poly.pizza", "quaternius.com", "opengameart.org")
LICENSES = ("CC0", "CC-BY")
PART = {"type": "object", "additionalProperties": False, "required": ["shape", "size"],
        "properties": {
            "shape": {"type": "string", "enum": list(assets.SHAPES)},
            "size": {**arr(NUM), "description": "[width, height, depth] in meters"},
            "pos": {**arr(NUM), "description": "[x, y, z] center; y is height above the floor"},
            "rot": {**arr(NUM), "description": "[x, y, z] rotation in radians"},
            "color": {"type": "string", "description": "#rrggbb"},
            "emissive": {"type": "string", "description": "glow color, e.g. for screens and lamps"},
            "opacity": {"type": "number", "description": "below 1 for glass"}}}


def btool(name: str, description: str, params: dict, **kw: Any):
    return tool(name, description, params, category="world-building", **kw)


def _err(fn):
    async def inner(args: dict, ctx: ToolContext) -> str:
        try:
            return await fn(args, ctx)
        except (BuildError, assets.AssetError, spatial.PlaceError,
                customtools.CustomToolError) as e:
            raise ToolError(str(e)) from None
    inner.__name__ = fn.__name__
    return inner


async def _org(ctx: ToolContext) -> Org:
    """The agent's own organization, fresh (another builder may have just changed it)."""
    async with SessionLocal() as session:
        return await session.get(Org, ctx.org.id)


# --- looking ----------------------------------------------------------------------------------


@btool("world_plan", "The space as a plan: its size and floor, every room with its rectangle, "
       "and everything in it with id, position, rotation and who added it. Coordinates are "
       "meters: x to the east, z to the south, from the north-west corner.", obj({}))
@_err
async def world_plan(args: dict, ctx: ToolContext) -> str:
    return await wb.describe(await _org(ctx))


@btool("world_catalog", "Search what can be placed: built-in furniture and kits (office, "
       "kitchen, industrial machines, sci-fi, city buildings, vehicles...) and the 3D assets "
       "people have made or imported. Empty query lists the categories.",
       obj({"query": S}))
@_err
async def world_catalog(args: dict, ctx: ToolContext) -> str:
    q = (args.get("query") or "").strip()
    made = await assets.all_assets()
    if not q:
        cats: dict[str, int] = {}
        for e in wb.catalog().values():
            cats[e["category"]] = cats.get(e["category"], 0) + 1
        return ("Categories: " + ", ".join(f"{c} ({n})" for c, n in sorted(cats.items()))
                + f". Assets made or imported: {len(made)}. Search with a word, e.g. 'sofa'.")
    lines = [f"- {k}: {e['label']} [{e['category']}, {e['size'][0]} x {e['size'][1]} m"
             f"{', seats ' + str(e['seats']) if e['seats'] else ''}]" for k, e in wb.search(q)]
    lines += [f"- {a['key']}: {a['label']} [asset, {a['size'][0]} x {a['size'][1]} m]" for a in made
              if wb.matches(q, f"{a['key']} {a['label']} {a['category']}")][:20]
    return "\n".join(lines) or f"Nothing matches '{q}'. Make it with world_make_asset, or find a free model."


# --- changing the layout ----------------------------------------------------------------------


@btool("world_place", "Put furniture or an asset into the space: at x, z (meters, its center) "
       "or anywhere free in a room. rot is degrees (0 faces south). y lifts it (e.g. onto a "
       "table). Returns its id.",
       obj({"item": S, "x": NUM, "z": NUM, "room": S, "rot": NUM, "y": NUM}, ["item"]))
@_err
async def world_place(args: dict, ctx: ToolContext) -> str:
    it = await wb.place(await _org(ctx), args["item"], x=args.get("x"), z=args.get("z"),
                        rot_deg=float(args.get("rot") or 0), room=args.get("room"),
                        y=args.get("y"), by=ctx.agent.name)
    return f"Placed {it['label']} ({it['id']}) at {it['x']}, {it['z']}."


@btool("world_move", "Move, turn or lift something already in the space (by id from world_plan).",
       obj({"id": S, "x": NUM, "z": NUM, "rot": NUM, "y": NUM}, ["id"]))
@_err
async def world_move(args: dict, ctx: ToolContext) -> str:
    it = await wb.move(await _org(ctx), args["id"], x=args.get("x"), z=args.get("z"),
                       rot_deg=args.get("rot"), y=args.get("y"), by=ctx.agent.name)
    return f"Moved {it['id']} to {it['x']}, {it['z']} (rot {round(math.degrees(it['rot']))}°)."


@btool("world_remove", "Take something out of the space (by id). People's desks stay.",
       obj({"id": S}, ["id"]))
@_err
async def world_remove(args: dict, ctx: ToolContext) -> str:
    return f"Removed {await wb.remove(await _org(ctx), args['id'], ctx.agent.name)}."


@btool("world_room", "Add a room, or change one with the same name: a rectangle (x, z, w, d "
       "in meters), its type (meeting, lounge, kitchen, team, focus, reception, custom), walls "
       "(none, glass, solid) and which side its door is on (n, s, e, w).",
       obj({"name": S, "type": S, "x": NUM, "z": NUM, "w": NUM, "d": NUM, "walls": S, "door": S},
           ["name", "x", "z", "w", "d"]))
@_err
async def world_room(args: dict, ctx: ToolContext) -> str:
    r = await wb.set_room(await _org(ctx), args["name"], type_=args.get("type") or "custom",
                          x=args["x"], z=args["z"], w=args["w"], d=args["d"],
                          walls=args.get("walls") or "none", door=args.get("door") or "s",
                          by=ctx.agent.name)
    return f"The {r['name']} is at x {r['x']}..{r['x'] + r['w']}, z {r['z']}..{r['z'] + r['d']}."


@btool("world_resize", "Make the space bigger or smaller (10-120 m each way) and/or change its "
       "floor: oak, walnut, concrete, polished, slate, epoxy, carpet, tile, grass, asphalt.",
       obj({"width": NUM, "depth": NUM, "floor": S}))
@_err
async def world_resize(args: dict, ctx: ToolContext) -> str:
    d = await wb.resize(await _org(ctx), width=args.get("width"), depth=args.get("depth"),
                        floor=args.get("floor"), by=ctx.agent.name)
    return f"The space is {d['width']} x {d['depth']} m with a {d['floor']} floor."


@btool("world_undo", "Undo the last change to the layout (by anyone).", obj({}))
@_err
async def world_undo(args: dict, ctx: ToolContext) -> str:
    return (await wb.undo(await _org(ctx))).capitalize() + "."


@btool("world_animate", "Animate something in the space: spin (turns per second, around its "
       "vertical axis), bob (meters up and down), pulse (how much it grows and shrinks, e.g. "
       "0.1), swing (degrees side to side), or clip (an animation inside an imported model). "
       "All zero/empty stops it.",
       obj({"id": S, "spin": NUM, "bob": NUM, "pulse": NUM, "swing": NUM, "clip": S}, ["id"]))
@_err
async def world_animate(args: dict, ctx: ToolContext) -> str:
    anim = {k: args.get(k) for k in (*wb.ANIMS, "clip")}
    it = await wb.animate(await _org(ctx), args["id"], anim, ctx.agent.name)
    return (f"{it['id']} now: " + ", ".join(f"{k} {v}" for k, v in it["anim"].items())
            if it.get("anim") else f"{it['id']} is still.")


# --- making and finding 3D models -------------------------------------------------------------


@btool("world_make_asset", "Build a 3D model from colored primitives (box, cylinder, sphere, "
       "cone): parts around its center on the floor (y = height). Parts can glow (emissive) "
       "or be glass (opacity). carryable makes it a small object people can pick up.",
       obj({"label": S, "parts": arr(PART), "category": S, "carryable": B, "description": S},
           ["label", "parts"]))
@_err
async def world_make_asset(args: dict, ctx: ToolContext) -> str:
    a = await assets.create_procedural(
        args["label"], args["parts"], category=args.get("category") or "Props",
        carryable=bool(args.get("carryable")), description=args.get("description", ""),
        created_by=ctx.agent.name)
    size = " x ".join(str(v) for v in a.size)
    return f"Made asset:{a.id} ({a.label}, {size} m). Place it with world_place."


def _license(value: str) -> str:
    lic = value.strip()
    if not any(lic.upper().startswith(ok) for ok in LICENSES):
        raise ToolError("only free models: CC0 or CC-BY (say which, and credit the author for CC-BY)")
    return lic


@btool("world_import_model", "Bring in a free 3D model from the web: a .glb, or a zip of "
       "models (like a Kenney kit) plus the model's file name (without one you get the list). "
       "Only from free libraries (kenney.nl, poly.pizza, quaternius.com, opengameart.org) and "
       "only CC0 or CC-BY; give its real size so it fits the world.",
       obj({"url": S, "file": S, "label": S, "license": S,
            "size_m": {**NUM, "description": "largest dimension in meters"},
            "height_m": {**NUM, "description": "or: its height in meters"},
            "category": S, "carryable": B}, ["url", "label", "license"]),
       side_effects=True, timeout=240)
@_err
async def world_import_model(args: dict, ctx: ToolContext) -> str:
    host = (urlparse(args["url"]).hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in FREE_HOSTS):
        raise ToolError(f"{host or 'that address'} isn't one of the free model libraries "
                        f"({', '.join(FREE_HOSTS)}); ask the user, or Zeus, to import it")
    lic = _license(args["license"])
    if not args.get("size_m") and not args.get("height_m"):
        raise ToolError("give size_m or height_m so it gets a real-world size")
    data = await assets.download(args["url"])
    if data[:4] != b"glTF":
        data = assets.glb_from_zip(data, args.get("file"))
    a = await assets.create_glb(data, args["label"], size_m=args.get("size_m"),
                                height_m=args.get("height_m"),
                                category=args.get("category") or "Props",
                                carryable=bool(args.get("carryable")), license_=lic,
                                created_by=ctx.agent.name,
                                source_url=args["url"] + (f"#{args['file']}" if args.get("file") else ""))
    return f"Imported asset:{a.id} ({a.label}, {lic}). Place it with world_place."


@btool("world_import_file", "Turn a .glb model in the organization's Files (e.g. one made in "
       "Blender) into an asset. Give its real size.",
       obj({"file": S, "label": S, "license": S, "size_m": NUM, "height_m": NUM, "category": S,
            "carryable": B}, ["file", "label"]))
@_err
async def world_import_file(args: dict, ctx: ToolContext) -> str:
    a = await att.get(args["file"])
    if a is None or a.org_id != ctx.org.id:
        rows = await att.for_org(ctx.org.id)
        a = next((r for r in rows if r.name.lower() == args["file"].strip().lower()), None)
    if a is None:
        raise ToolError(f"no file '{args['file']}' in Files")
    if not args.get("size_m") and not args.get("height_m"):
        raise ToolError("give size_m or height_m so it gets a real-world size")
    asset = await assets.create_glb(att.file_path(a).read_bytes(), args["label"],
                                    size_m=args.get("size_m"), height_m=args.get("height_m"),
                                    category=args.get("category") or "Props",
                                    carryable=bool(args.get("carryable")),
                                    license_=args.get("license") or "made for this organization",
                                    created_by=ctx.agent.name, source_url=f"file:{a.name}")
    return f"Imported asset:{asset.id} ({asset.label}) from {a.name}. Place it with world_place."


# --- things people do and use -----------------------------------------------------------------


@btool("world_make_action", "Create something people here can do, as a tool: a narration with "
       "{actor}, {target} and {room} (\"{actor} rings the bell in the {room}\"), an optional "
       "gesture (wave, nod, shake_head, thumbs_up) and an object they must hold "
       "(requires_object, e.g. '*bell*'). It's given to you and the colleagues you name.",
       obj({"name": {**S, "description": "lower_case_with_underscores"}, "description": S,
            "narration": S, "gesture": {"type": "string", "enum": list(GESTURES)},
            "requires_object": S, "give_to": arr(S)}, ["name", "description", "narration"]))
@_err
async def world_make_action(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        existing = await session.get(CustomTool, args["name"])
    if existing is not None and existing.created_by != ctx.agent.name:
        raise ToolError(f"there's already a tool called '{args['name']}'; pick another name")
    cfg: dict[str, Any] = {"narration": args["narration"], "witness": True}
    if args.get("gesture"):
        cfg["gesture"] = args["gesture"]
    if args.get("requires_object"):
        cfg["requires_object"] = args["requires_object"]
    row = await customtools.save(args["name"], args["description"], "action", None, cfg, "auto",
                                 created_by=ctx.agent.name)
    names = {n.lower() for n in [ctx.agent.name, *(args.get("give_to") or [])]}
    given = []
    async with SessionLocal() as session:
        team = (await session.execute(select(Agent).where(Agent.org_id == ctx.org.id))).scalars().all()
    for agent in team:
        if agent.name.lower() in names and \
                not any(t.get("name") == row.name for t in agent.tools or []):
            await orgs.update_agent(agent.id, {"tools": [*(agent.tools or []),
                                                         {"name": row.name, "approval": "auto"}]})
            given.append(agent.name)
    return f"'{row.name}' is ready" + (f" for {', '.join(given)}." if given else ".")


@btool("world_spawn", "Put a movable object in the space, lying in a room or held by someone; "
       "people can pick it up, give it, put it down and use it. asset gives it a look.",
       obj({"name": S, "where": {**S, "description": "a room name or a person"}, "asset": S,
            "description": S, "state": {"type": "object"}}, ["name", "where"]))
@_err
async def world_spawn(args: dict, ctx: ToolContext) -> str:
    from app.services import comms

    w = await spatial.load_world(ctx.org.id)
    async with SessionLocal() as session:
        holder = await comms.resolve_agent(session, ctx.org.id, args["where"])
    place: dict[str, Any] = {}
    if holder is None:
        room = w.space.room(args["where"])
        if room is None:
            raise ToolError(f"'{args['where']}' is neither a room nor a person here")
        place = {"kind": "room", "room": room.name}
    look = ""
    if args.get("asset"):
        found = await assets.resolve_key(args["asset"])
        look = found["key"] if found else args["asset"]
    o = WorldObject(org_id=ctx.org.id, name=args["name"].strip()[:80], asset=look,
                    description=args.get("description", ""), holder_id=holder.id if holder else None,
                    place=place, state=args.get("state") or {})
    async with SessionLocal() as session:
        session.add(o)
        await session.commit()
    await bus.publish("world.object", object_dict(o), org_id=ctx.org.id)
    return f"{o.name} ({o.id}) is " + (f"with {holder.name}." if holder else f"in the {place['room']}.")


@btool("world_remove_object", "Take a movable object out of the space.", obj({"object": S}, ["object"]))
@_err
async def world_remove_object(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        rows = (await session.execute(select(WorldObject).where(
            WorldObject.org_id == ctx.org.id))).scalars().all()
        q = args["object"].strip().lower()
        o = next((r for r in rows if r.id == args["object"] or r.name.lower() == q), None)
        if o is None:
            raise ToolError(f"no object '{args['object']}' here")
        await session.delete(o)
        await session.commit()
    await bus.publish("world.object.removed", {"id": o.id}, org_id=ctx.org.id)
    return f"Removed {o.name}."


BUILDER_TOOLS = ["world_plan", "world_catalog", "world_place", "world_move", "world_remove",
                 "world_room", "world_resize", "world_undo", "world_animate", "world_make_asset",
                 "world_import_model", "world_import_file", "world_make_action", "world_spawn",
                 "world_remove_object"]
