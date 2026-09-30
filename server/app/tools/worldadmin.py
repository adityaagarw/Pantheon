"""Zeus's tools for the physical world: 3D assets, furniture, objects, scene rules."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.ids import new_id
from app.events import bus
from app.models import WorldObject
from app.services import assets, orgs, spatial
from app.tools.base import B, S, ToolContext, ToolError, arr, obj
from app.tools.meta import _agent, _dump, _org, meta
from app.tools.world import object_dict

NUM = {"type": "number"}
PART = {"type": "object", "additionalProperties": False, "required": ["shape", "size"],
        "properties": {
            "shape": {"type": "string", "enum": list(assets.SHAPES)},
            "size": {**arr(NUM), "description": "[width, height, depth] in meters"},
            "pos": {**arr(NUM), "description": "[x, y, z] center in meters; y is height above "
                                               "the floor, x/z relative to the item's center"},
            "rot": {**arr(NUM), "description": "[x, y, z] rotation in radians"},
            "color": {"type": "string", "description": "#rrggbb"}}}


def _asset_err(fn):
    async def inner(args: dict, ctx: ToolContext) -> str:
        try:
            return await fn(args, ctx)
        except (assets.AssetError, spatial.PlaceError) as e:
            raise ToolError(str(e)) from None
    inner.__name__ = fn.__name__
    return inner


@meta("list_assets", "List the 3D assets available beyond the built-in furniture: ones you "
      "built or imported, and ones from plugins.", obj({}))
async def list_assets(args: dict, ctx: ToolContext) -> str:
    rows = await assets.all_assets()
    if not rows:
        return "No custom assets yet. Built-in furniture is always available."
    return "\n".join(f"- {a['key']}: {a['label']} [{a['category']}, {a['kind']}, "
                     f"{'x'.join(str(v) for v in a['size'])} m"
                     f"{', carryable' if a['carryable'] else ''}]" for a in rows)


@meta("create_asset", "Build a simple 3D model from colored primitives (boxes, cylinders, "
      "spheres, cones). Parts are placed around the item's center on the floor (y = height). "
      "Example mug: a cylinder size [0.08, 0.1, 0.08] pos [0, 0.05, 0].",
      obj({"label": S, "parts": arr(PART), "category": S, "carryable": B, "description": S},
          ["label", "parts"]))
@_asset_err
async def create_asset(args: dict, ctx: ToolContext) -> str:
    a = await assets.create_procedural(
        args["label"], args["parts"], category=args.get("category") or "Props",
        carryable=bool(args.get("carryable")), description=args.get("description", ""),
        created_by=ctx.agent.name)
    return f"Created asset asset:{a.id} ({a.label}, {'x'.join(str(v) for v in a.size)} m)."


@meta("import_asset", "Download a .glb 3D model from a URL and add it as an asset, scaled to a "
      "real-world size. Only use models whose license allows it (CC0 / CC-BY) and record it.",
      obj({"url": S, "label": S, "license": S,
           "size_m": {**NUM, "description": "largest dimension in meters"},
           "height_m": {**NUM, "description": "or: height in meters"},
           "category": S, "carryable": B, "description": S}, ["url", "label", "license"]),
      approval="ask", side_effects=True, timeout=180)
@_asset_err
async def import_asset(args: dict, ctx: ToolContext) -> str:
    if not args.get("size_m") and not args.get("height_m"):
        raise ToolError("give size_m or height_m so the model gets a real-world size")
    a = await assets.import_glb(
        args["url"], args["label"], size_m=args.get("size_m"), height_m=args.get("height_m"),
        category=args.get("category") or "Props", carryable=bool(args.get("carryable")),
        license_=args["license"], description=args.get("description", ""),
        created_by=ctx.agent.name)
    return f"Imported asset:{a.id} ({a.label}, {'x'.join(str(v) for v in a.size)} m)."


@meta("place_item", "Put a piece of furniture or an asset into a room of an organization's "
      "space (it finds a free spot). item: a built-in furniture key (e.g. 'whiteboard', "
      "'loungeChair') or an asset key/label.",
      obj({"org": S, "item": S, "room": S, "label": S}, ["org", "item", "room"]))
@_asset_err
async def place_item(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    w = await spatial.load_world(org.id)
    room = w.space.room(args["room"])
    if room is None:
        raise ToolError(f"no room '{args['room']}' (rooms: "
                        f"{', '.join(r.name for r in w.space.rooms)})")
    found = await assets.resolve_key(args["item"])
    kind = found["key"] if found else args["item"].strip()
    layout = dict(org.layout or {})
    extras = list(layout.get("extras") or [])
    extras.append({"id": new_id("ex"), "kind": kind, "room": room.name,
                   "label": args.get("label") or (found or {}).get("label")})
    layout["extras"] = extras
    await orgs.update_org(org.id, {"layout": layout})
    return f"Placed {args.get('label') or kind} in the {room.name}."


async def _place_for(w: spatial.World, ref: str) -> tuple[dict[str, Any], str | None]:
    async with SessionLocal() as session:
        from app.services import comms

        holder = await comms.resolve_agent(session, w.org.id, ref)
    if holder is not None:
        return {}, holder.id
    room = w.space.room(ref)
    if room is None:
        raise ToolError(f"'{ref}' is neither a room nor a person here")
    return {"kind": "room", "room": room.name}, None


@meta("spawn_object", "Add a movable object to an organization's space, lying in a room or held "
      "by someone. Agents can pick_up, give, put_down and use_object it.",
      obj({"org": S, "name": S, "where": {**S, "description": "a room name or a person"},
           "asset": {**S, "description": "optional look: asset key/label or furniture key"},
           "description": S, "state": {"type": "object"}}, ["org", "name", "where"]))
@_asset_err
async def spawn_object(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    w = await spatial.load_world(org.id)
    place, holder = await _place_for(w, args["where"])
    look = ""
    if args.get("asset"):
        found = await assets.resolve_key(args["asset"])
        look = found["key"] if found else args["asset"]
    o = WorldObject(org_id=org.id, name=args["name"].strip()[:80], asset=look,
                    description=args.get("description", ""), holder_id=holder, place=place,
                    state=args.get("state") or {})
    async with SessionLocal() as session:
        session.add(o)
        await session.commit()
    await bus.publish("world.object", object_dict(o), org_id=org.id)
    return f"Spawned {o.name} ({o.id}) " + (f"held by {w.by_id[holder].name}" if holder
                                            else f"in the {place['room']}") + "."


@meta("remove_object", "Remove an object from an organization's space.",
      obj({"org": S, "object": S}, ["org", "object"]))
async def remove_object(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    async with SessionLocal() as session:
        rows = (await session.execute(select(WorldObject).where(
            WorldObject.org_id == org.id))).scalars().all()
        q = args["object"].strip().lower()
        o = next((r for r in rows if r.id == args["object"] or r.name.lower() == q), None)
        if o is None:
            raise ToolError(f"no object '{args['object']}' in {org.name}")
        await session.delete(o)
        await session.commit()
    await bus.publish("world.object.removed", {"id": o.id}, org_id=org.id)
    return f"Removed {o.name}."


@meta("set_world", "Configure an organization's physical space: enabled (agents get bodies "
      "and presence tools), rules (the scene's premise and rules, added to every agent's "
      "prompt), witness (people in a room notice each other's movements and actions).",
      obj({"org": S, "enabled": B, "rules": S, "witness": B}, ["org"]))
async def set_world(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    world = dict((org.settings or {}).get("world") or {})
    for k in ("enabled", "rules", "witness"):
        if k in args:
            world[k] = args[k]
    await orgs.update_org(org.id, {"settings": {"world": world}})
    return f"{org.name} world settings: {_dump(world)}"


@meta("move_agent", "Direct an agent to a place in its organization's space (scene direction).",
      obj({"org": S, "agent": S, "place": S}, ["org", "agent", "place"]))
@_asset_err
async def move_agent(args: dict, ctx: ToolContext) -> str:
    org = await _org(args["org"])
    agent = await _agent(org, args["agent"])
    w = await spatial.load_world(org.id)
    me = w.by_id.get(agent.id, agent)
    loc = spatial.resolve_place(args["place"], w.space, w.agents, me)
    await spatial.set_location(me, loc, "is directed to " + args["place"])
    return f"{me.name} is now {w.where(me)}."
