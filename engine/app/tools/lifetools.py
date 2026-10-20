"""Life tools: residents of a living world look after their needs and chase their wants.

They come with an organization that has life turned on (settings.life.enabled).
"""

from __future__ import annotations

from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, Org
from app.services import life, spatial
from app.services import worldbuild as wb
from app.tools.base import S, ToolContext, ToolError, obj, tool

AMBIENT = ("life_status", "life_do", "life_want")


def ltool(name: str, description: str, params: dict):
    return tool(name, description, params, category="life")


async def _fresh(ctx: ToolContext) -> tuple[Agent, Org]:
    async with SessionLocal() as session:
        me = await session.get(Agent, ctx.agent.id)
        org = await session.get(Org, ctx.org.id)
    if me is None or org is None:
        raise ToolError("not found")
    if not life.enabled(org) or not (me.life or {}).get("needs"):
        raise ToolError("life isn't turned on in this organization")
    return me, org


@ltool("life_status", "How you feel (each need from 0 to 100), your mood, traits and wants, and "
       "the nearest places in the world to look after each need, or what's missing.", obj({}))
async def life_status(args: dict, ctx: ToolContext) -> str:
    me, org = await _fresh(ctx)
    design = wb.design_of(org)
    lines = [life.prompt_section(me, org).split("\n" + life.LIFE_RULES)[0], "", "Where you could:"]
    for name, act in life.ACTIVITIES.items():
        if act.get("people"):
            w = await spatial.load_world(org.id)
            here = [a.name for a in w.people_in(w.room_of(w.by_id.get(me.id, me)), exclude=me.id)]
            lines.append("- chat: " + (f"with {', '.join(here)} (here)" if here else
                                        "nobody is here; move_to someone first"))
            continue
        spots = await life.find_spots(design, name, me)
        lines.append(f"- {name}: " + ("; ".join(
            f"{s['label']} [{s['id']}]" + (f" in the {s['room']}" if s["room"] else "")
            + (" (yours)" if s["mine"] else "") for s in spots)
            if spots else f"nowhere yet. Build {act['build']}."))
    return "\n".join(lines)


@ltool("life_do", "Do something for yourself: sleep, rest, eat, wash, toilet, play, nature or "
       "chat. You walk to the nearest suitable thing (your own first), or to the item you name, "
       "and your needs go up. If the world has nothing suitable, you're told what to build.",
       obj({"activity": {"type": "string", "enum": list(life.ACTIVITIES)},
            "at": {**S, "description": "item id or name to use (optional)"},
            "with": {**S, "description": "chat: who with (they must be in the same room)"}},
           ["activity"]))
async def life_do(args: dict, ctx: ToolContext) -> str:
    me, org = await _fresh(ctx)
    name = args["activity"]
    act = life.ACTIVITIES.get(name)
    if act is None:
        raise ToolError(f"activity must be one of {', '.join(life.ACTIVITIES)}")
    w = await spatial.load_world(org.id)
    body = w.by_id.get(me.id, me)
    if act.get("people"):
        here = w.people_in(w.room_of(body), exclude=me.id)
        if args.get("with"):
            here = [a for a in here if a.name.lower().startswith(str(args["with"]).lower())]
        if not here:
            raise ToolError("Nobody " + (f"called {args['with']} " if args.get("with") else "")
                            + "is here to chat with. move_to someone first (or send_message).")
        await life.adjust(me, org, act["gives"], doing="chatting with " + here[0].name)
        for other in here[:3]:
            if (other.life or {}).get("needs"):
                await life.adjust(other, org, {"social": act["gives"]["social"] / 2})
        await bus.publish("agent.emote", {"emote": "wave", "at": here[0].name}, org_id=org.id, agent_id=me.id)
        return (f"You chat with {', '.join(a.name for a in here[:3])}. You feel less lonely. "
                "Say what's on your mind with say_aloud.")
    design = wb.design_of(org)
    spots = await life.find_spots(design, name, me, limit=200)
    if args.get("at"):
        ref = str(args["at"]).lower()
        spots = [s for s in spots if s["id"] == args["at"] or ref in s["label"].lower()]
    if not spots:
        raise ToolError(f"There's nowhere to {name} in the world yet. Build {act['build']}, "
                        "then try again.")
    spot = spots[0]
    loc = {"kind": "item", "room": spot["room"] or spatial.OPEN_FLOOR, "itemId": spot["id"],
           "itemKind": spot["kind"], "label": spot["label"]}
    await spatial.set_location(me, loc, f"{act['verb']} at the {spot['label']}")
    if gesture := life.EMOTES.get(name):
        await bus.publish("agent.emote", {"emote": gesture, "at": ""}, org_id=org.id, agent_id=me.id)
    after = await life.adjust(me, org, act["gives"], doing=f"{name} at the {spot['label']}")
    needs = after["needs"]
    better = ", ".join(f"{n} {needs[n]}" for n in act["gives"])
    return (f"You {act['you']} at the {spot['label']}" + (f" in the {spot['room']}" if spot["room"] else "")
            + f"{' (yours)' if spot['mine'] else ''}. Now: {better}.")


@ltool("life_want", "Your wants, your own goals: add one you've just thought of, mark one done "
       "when you've made it happen (that feels good), or drop one you no longer care about.",
       obj({"action": {"type": "string", "enum": ["add", "done", "drop"]},
            "text": {**S, "description": "add: what you want; done/drop: part of its text"},
            "id": S}, ["action"]))
async def life_want(args: dict, ctx: ToolContext) -> str:
    me, org = await _fresh(ctx)
    try:
        return await life.want(me, org, args["action"], str(args.get("text") or ""),
                               str(args.get("id") or ""))
    except life.LifeError as e:
        raise ToolError(str(e)) from None
