"""Presence tools: every agent has a body in its organization's physical space.

They are granted automatically to agents in orgs where the world is enabled
(Org settings → Physical space), unless an agent's tool list denies them.
Movement and objects are visible in the 3D office; speech and (optionally)
actions are heard/seen by whoever is in the same room.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app import plugins
from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, WorldObject
from app.services import comms, spatial
from app.services.comms import Sender
from app.tools.base import S, ToolContext, ToolError, obj, tool

AMBIENT = ("look_around", "move_to", "emote", "say_aloud", "pick_up", "put_down", "give",
           "use_object")
GESTURES = {"wave": "wave", "nod": "yes", "shake_head": "no", "thumbs_up": "thumbs"}


def world_tool(name: str, description: str, params: dict[str, Any]):
    return tool(name, description, params, category="world")


async def _world(ctx: ToolContext) -> spatial.World:
    if not spatial.world_enabled(ctx.org):
        raise ToolError("this organization has no physical space")
    return await spatial.load_world(ctx.org.id)


def _me(w: spatial.World, ctx: ToolContext) -> Agent:
    return w.by_id.get(ctx.agent.id) or ctx.agent


def _find_object(w: spatial.World, me: Agent, ref: str, *, held: bool | None = None
                 ) -> WorldObject:
    q = ref.strip().lower()
    here = w.room_of(me)
    candidates = [o for o in w.objects if held is None or (o.holder_id == me.id) == held]

    def score(o: WorldObject) -> tuple[int, int]:
        name = o.name.lower()
        match = 0 if (name == q or o.id == ref) else 1 if q in name else 2
        near = 0 if o.holder_id == me.id else 1 if w.object_room(o) == here else 2
        return match, near

    hits = sorted((o for o in candidates if score(o)[0] < 2), key=score)
    if not hits:
        if held:
            raise ToolError(f"you're not holding '{ref}'")
        raise ToolError(f"there's no '{ref}' around (look_around lists the objects)")
    return hits[0]


async def _witness(w: spatial.World, ctx: ToolContext, room: str, text: str,
                   exclude: set[str]) -> None:
    """Tell everyone else in the room what they saw (if the org wants witnessing)."""
    if not ((w.org.settings or {}).get("world") or {}).get("witness"):
        return
    people = [a.id for a in w.people_in(room) if a.id not in exclude]
    if people:
        await comms.post(w.org.id, Sender.system(ctx.depth), text,
                         to_agents=people, kind="observation", meta={"room": room})


async def _save(o: WorldObject) -> None:
    async with SessionLocal() as session:
        row = await session.get(WorldObject, o.id)
        if row is None:
            raise ToolError("that object no longer exists")
        row.holder_id, row.place, row.state = o.holder_id, dict(o.place or {}), dict(o.state or {})
        await session.commit()
    await bus.publish("world.object", object_dict(o), org_id=o.org_id)


def object_dict(o: WorldObject) -> dict[str, Any]:
    return {"id": o.id, "name": o.name, "asset": o.asset, "description": o.description,
            "holderId": o.holder_id, "place": o.place or {}, "state": o.state or {}}


def _here_place(w: spatial.World, me: Agent) -> dict[str, Any]:
    loc = me.location or {}
    if loc.get("kind") in ("room", "item"):
        return {k: v for k, v in loc.items() if k in ("kind", "room", "itemId", "itemKind",
                                                     "label")}
    return {"kind": "room", "room": w.room_of(me), "nearAgent": me.id}


@world_tool("look_around", "See your surroundings: where you are, who is with you, the rooms "
            "and what's in them, where everyone is, and the objects lying around.", obj({}))
async def look_around(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    return spatial.describe(w, _me(w, ctx))


@world_tool("move_to", "Walk somewhere in the physical space: a room (\"Lounge\"), a colleague "
            "(\"Ada\"), an item (\"coffee machine\"), or \"my desk\". Others see you move.",
            obj({"place": S, "reason": {"type": "string", "description": "optional, shown to "
                                        "observers"}}, ["place"]))
async def move_to(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    try:
        loc = spatial.resolve_place(args["place"], w.space, w.agents, me)
    except spatial.PlaceError as e:
        raise ToolError(str(e)) from None
    before = w.room_of(me)
    text = ("goes back to their desk" if loc is None else
            f"walks over to {loc['name']}" if loc["kind"] == "agent" else
            f"goes to the {loc['label']} in the {loc['room']}" if loc["kind"] == "item" else
            f"goes to the {loc['room']}")
    await spatial.set_location(me, loc, text)
    after = w.room_of(me)
    if after != before:
        await _witness(w, ctx, after, f"{me.name} {text}"
                       + (f" ({args['reason']})" if args.get("reason") else "") + ".", {me.id})
    company = [a.name for a in w.people_in(after, exclude=me.id)]
    you = (text.replace("goes", "go", 1).replace("walks", "walk", 1)
           .replace("their", "your", 1))
    return (f"You {you}. You're now {w.where(me, you=True)}. "
            f"Here: {', '.join(company) or 'nobody else'}.")


@world_tool("emote", "Make a visible gesture: wave, nod, shake_head or thumbs_up.",
            obj({"gesture": {"type": "string", "enum": list(GESTURES)}, "at": S}, ["gesture"]))
async def emote(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    target = args.get("at") or ""
    await bus.publish("agent.emote", {"emote": GESTURES[args["gesture"]], "at": target},
                      org_id=w.org.id, agent_id=me.id)
    await _witness(w, ctx, w.room_of(me),
                   f"{me.name} gestures: {args['gesture'].replace('_', ' ')}"
                   + (f" at {target}" if target else "") + ".", {me.id})
    return f"You {args['gesture'].replace('_', ' ')}{' at ' + target if target else ''}."


@world_tool("say_aloud", "Say something out loud. Everyone in the same room hears it (it's not "
            "a message: people elsewhere don't). Use send_message for someone who isn't here.",
            obj({"text": S}, ["text"]))
async def say_aloud(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    room = w.room_of(me)
    listeners = [a.id for a in w.people_in(room, exclude=me.id)]
    await comms.post(w.org.id, ctx.sender, args["text"], to_agents=listeners, kind="speech",
                     meta={"room": room})
    names = [w.by_id[a].name for a in listeners]
    return f"You said it out loud in the {room}. " + (
        f"Heard by: {', '.join(names)}." if names else "Nobody else is here to hear it.")


@world_tool("pick_up", "Pick up an object near you.", obj({"object": S}, ["object"]))
async def pick_up(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    o = _find_object(w, me, args["object"], held=False)
    if o.holder_id and o.holder_id in w.by_id:
        raise ToolError(f"{w.by_id[o.holder_id].name} is holding the {o.name}")
    room = w.object_room(o)
    if room != w.room_of(me):
        raise ToolError(f"the {o.name} is in the {room}; move_to there first")
    if o.state.get("fixed"):
        raise ToolError(f"the {o.name} can't be carried")
    o.holder_id, o.place = me.id, {}
    await _save(o)
    await _witness(w, ctx, room, f"{me.name} picks up the {o.name}.", {me.id})
    return f"You pick up the {o.name}."


@world_tool("put_down", "Put down an object you're holding where you are.",
            obj({"object": S}, ["object"]))
async def put_down(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    o = _find_object(w, me, args["object"], held=True)
    o.holder_id, o.place = None, _here_place(w, me)
    await _save(o)
    room = w.room_of(me)
    await _witness(w, ctx, room, f"{me.name} puts down the {o.name}.", {me.id})
    return f"You put the {o.name} down in the {room}."


@world_tool("give", "Hand an object you're holding to someone in the same room.",
            obj({"object": S, "to": S}, ["object", "to"]))
async def give(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    o = _find_object(w, me, args["object"], held=True)
    async with SessionLocal() as session:
        other = await comms.resolve_agent(session, w.org.id, args["to"])
    if other is None or other.id == me.id:
        raise ToolError(f"no one called '{args['to']}' here")
    other = w.by_id.get(other.id, other)
    room = w.room_of(me)
    if w.room_of(other) != room:
        raise ToolError(f"{other.name} is {w.where(other)}; move_to them first")
    o.holder_id = other.id
    await _save(o)
    await comms.post(w.org.id, Sender.system(ctx.depth),
                     f"{me.name} hands you the {o.name}.", to_agents=[other.id],
                     kind="observation", meta={"room": room})
    await _witness(w, ctx, room, f"{me.name} hands the {o.name} to {other.name}.",
                   {me.id, other.id})
    return f"You hand the {o.name} to {other.name}."


@world_tool("use_object", "Use an object you hold or that's near you, e.g. action \"drink\", "
            "\"turn on\", \"write on\", optionally on/with someone (target).",
            obj({"object": S, "action": S, "target": S}, ["object", "action"]))
async def use_object(args: dict, ctx: ToolContext) -> str:
    w = await _world(ctx)
    me = _me(w, ctx)
    o = _find_object(w, me, args["object"])
    room = w.room_of(me)
    if o.holder_id != me.id and w.object_room(o) != room:
        raise ToolError(f"the {o.name} is in the {w.object_room(o)}; move_to there first")
    target = None
    if args.get("target"):
        async with SessionLocal() as session:
            target = await comms.resolve_agent(session, w.org.id, args["target"])
        if target is None:
            raise ToolError(f"no one called '{args['target']}'")
        target = w.by_id.get(target.id, target)
        if w.room_of(target) != room:
            raise ToolError(f"{target.name} is {w.where(target)}, not here")
    event = plugins.UseEvent(o, args["action"], me, target, w, ctx)
    narration = await plugins.handle_use(event)
    await _save(o)
    what = f"{me.name} {args['action']}s the {o.name}" if len(args["action"].split()) == 1 \
        else f"{me.name} uses the {o.name}: {args['action']}"
    text = what + (f" on {target.name}" if target else "") + "."
    if narration:
        text += " " + " ".join(narration)
    await bus.publish("world.action", {"objectId": o.id, "action": args["action"],
                                       "targetId": target.id if target else None, "text": text},
                      org_id=w.org.id, agent_id=me.id)
    if target is not None:
        await comms.post(w.org.id, Sender.system(ctx.depth), text,
                         to_agents=[target.id], kind="observation", meta={"room": room})
    await _witness(w, ctx, room, text, {me.id, *([target.id] if target else [])})
    return text


async def objects_for(org_id: str) -> list[WorldObject]:
    async with SessionLocal() as session:
        return list((await session.execute(select(WorldObject).where(
            WorldObject.org_id == org_id).order_by(WorldObject.created_at))).scalars())
