"""The physical space an organization lives in, as agents understand it.

The 3D office is drawn by the browser from ``org.layout.office`` (a saved
design) or, when none is saved, from a default design generated from the
team. This module gives the server the same picture in words — rooms, what is
in them, who is where, and the movable objects lying around — so agents can
talk about their surroundings and move through them.

Locations are stored on ``Agent.location`` (None = the agent's default spot:
its desk, a meeting, a break) and published as ``agent.moved`` so every
viewer's office animates the walk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.events import bus
from app.models import Agent, Asset, Meeting, Org, WorldObject

# Items worth mentioning when describing a room (the rest is clutter).
NOTABLE_LABELS = {
    "workstation": "desk", "workstationLaptop": "desk", "conference6": "conference table",
    "conference8": "conference table", "huddleTable": "round table", "taskboard": "task board",
    "televisionModern": "TV", "kitchenFridgeLarge": "fridge", "kitchenSink": "sink",
    "kitchenCoffeeMachine": "coffee machine", "kitchenMicrowave": "microwave",
    "kitchenBar": "breakfast bar", "stoolBar": "bar stool", "loungeSofaLong": "long sofa",
    "loungeDesignSofa": "sofa", "loungeChair": "armchair", "tableCoffee": "coffee table",
    "bookcaseOpen": "bookcase", "bookcaseClosedWide": "bookcase", "lampRoundFloor": "floor lamp",
    "trashcan": "trash can", "pottedPlant": "potted plant", "whiteboard": "whiteboard",
}
CLUTTER = {"rugRectangle", "rugRound", "plantSmall1", "plantSmall2", "plantSmall3",
           "kitchenCabinet", "kitchenCabinetDrawer", "kitchenBarEnd", "cabinetTelevision",
           "wall", "wallWindow", "wallDoorway"}
DEFAULT_ROOM_ITEMS = {
    "Boardroom": ["conference8", "televisionModern", "pottedPlant"],
    "Huddle room": ["huddleTable"],
    "Lounge": ["loungeSofaLong", "loungeDesignSofa", "loungeChair", "tableCoffee",
               "televisionModern", "bookcaseOpen", "lampRoundFloor"],
    "Kitchen": ["kitchenFridgeLarge", "kitchenSink", "kitchenCoffeeMachine", "kitchenMicrowave",
                "kitchenBar", "stoolBar", "trashcan"],
}
OPEN_FLOOR = "open floor"


def humanize(kind: str) -> str:
    base = kind.split("/")[-1].split(":")[-1]
    return re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", base).lower()


@dataclass
class Item:
    id: str
    kind: str
    label: str
    room: str
    agent_id: str | None = None


@dataclass
class Room:
    name: str
    type: str
    rect: tuple[float, float, float, float] | None = None  # x, z, w, d
    items: list[Item] = field(default_factory=list)


@dataclass
class Space:
    rooms: list[Room]
    desks: dict[str, str]  # agent id -> room name
    saved: bool

    def room(self, name: str) -> Room | None:
        key = name.strip().lower()
        return next((r for r in self.rooms if r.name.lower() == key), None)

    def items(self) -> list[Item]:
        return [i for r in self.rooms for i in r.items]


def _label_for(kind: str, assets: dict[str, str]) -> str:
    if kind in assets:
        return assets[kind]
    return NOTABLE_LABELS.get(kind, humanize(kind))


async def _asset_labels() -> dict[str, str]:
    from app import plugins

    async with SessionLocal() as session:
        rows = (await session.execute(select(Asset.id, Asset.label))).all()
    out = {f"asset:{i}": label for i, label in rows}
    out.update({a["key"]: a["label"] for a in plugins.assets()})
    return out


def _sorted_agents(agents: list[Agent]) -> list[Agent]:
    return sorted(agents, key=lambda a: (a.created_at.isoformat() if a.created_at else "", a.id))


async def space_of(org: Org, agents: list[Agent]) -> Space:
    """Rooms, their notable items and each agent's desk room."""
    labels = await _asset_labels()
    names = {a.id: a.name for a in agents}
    design = (org.layout or {}).get("office")
    rooms: list[Room] = []
    desks: dict[str, str] = {}
    saved = isinstance(design, dict) and isinstance(design.get("rooms"), list) \
        and isinstance(design.get("items"), list)
    if saved:
        for r in design["rooms"]:
            try:
                rect = (float(r["x"]), float(r["z"]), float(r["w"]), float(r["d"]))
            except (KeyError, TypeError, ValueError):
                rect = None
            rooms.append(Room(str(r.get("name") or "Room"), str(r.get("type") or "custom"), rect))
        floor = Room(OPEN_FLOOR, "open")

        def room_at(x: float, z: float) -> Room:
            for rm in rooms:
                if rm.rect and rm.rect[0] <= x <= rm.rect[0] + rm.rect[2] \
                        and rm.rect[1] <= z <= rm.rect[1] + rm.rect[3]:
                    return rm
            return floor

        for it in design["items"]:
            kind = str(it.get("kind", ""))
            if kind in CLUTTER:
                continue
            try:
                rm = room_at(float(it.get("x", 0)), float(it.get("z", 0)))
            except (TypeError, ValueError):
                rm = floor
            owner = it.get("agentId") if it.get("agentId") in names else None
            label = _label_for(kind, labels)
            if owner:
                label = f"{names[owner]}'s desk"
                desks[owner] = rm.name
            rm.items.append(Item(str(it.get("id")), kind, label, rm.name, owner))
        if floor.items:
            rooms.append(floor)
        # People without a workstation (e.g. in scenes) belong to their team's room.
        for a in agents:
            if a.id not in desks and not a.is_supervisor:
                home = next((r for r in rooms if r.name.lower() == (a.team or "").lower()), None)
                if home is not None:
                    desks[a.id] = home.name
    else:
        teams: list[str] = []
        for a in _sorted_agents(agents):
            if a.is_supervisor:
                continue
            team = (a.team or "").strip() or "Team"
            if team not in teams:
                teams.append(team)
            desks[a.id] = team
        for t in teams or ["Team"]:
            rooms.append(Room(t, "team"))
        default = ["Boardroom", *(["Huddle room"] if len(agents) > 6 else []), "Lounge", "Kitchen"]
        types = {"Boardroom": "meeting", "Huddle room": "meeting", "Lounge": "lounge",
                 "Kitchen": "kitchen"}
        for name in default:
            rm = Room(name, types[name])
            rm.items = [Item(f"default:{name}:{k}", k, _label_for(k, labels), name)
                        for k in DEFAULT_ROOM_ITEMS[name]]
            rooms.append(rm)
        for aid, team in desks.items():
            rm = next(r for r in rooms if r.name == team)
            rm.items.append(Item(f"desk:{aid}", "workstation", f"{names[aid]}'s desk", team, aid))
    # Items placed by name (by Zeus or plugins) on top of either kind of design.
    for ex in (org.layout or {}).get("extras") or []:
        rm = next((r for r in rooms if r.name.lower() == str(ex.get("room", "")).lower()), None)
        if rm is not None:
            kind = str(ex.get("kind", ""))
            rm.items.append(Item(str(ex.get("id")), kind,
                                 str(ex.get("label") or _label_for(kind, labels)), rm.name))
    return Space(rooms, desks, bool(saved))


# --- where people are -------------------------------------------------------------------


async def _meeting_rooms(org_id: str) -> dict[str, str]:
    """agent id -> meeting room for meetings in progress."""
    async with SessionLocal() as session:
        rows = (await session.execute(select(Meeting).where(
            Meeting.org_id == org_id, Meeting.status == "running"))).scalars().all()
    out: dict[str, str] = {}
    for m in rows:
        for p in m.participants or []:
            out[p] = m.room or "the meeting room"
    return out


def room_of(agent: Agent, space: Space, by_id: dict[str, Agent], meetings: dict[str, str],
            _depth: int = 0) -> str:
    if agent.id in meetings:
        return meetings[agent.id]
    loc = agent.location or {}
    kind = loc.get("kind")
    if kind in ("room", "item") and loc.get("room"):
        return str(loc["room"])
    if kind == "agent" and _depth < 3 and loc.get("agentId") in by_id:
        return room_of(by_id[loc["agentId"]], space, by_id, meetings, _depth + 1)
    return space.desks.get(agent.id, OPEN_FLOOR)


def where_text(agent: Agent, space: Space, by_id: dict[str, Agent], meetings: dict[str, str],
               *, you: bool = False) -> str:
    if agent.id in meetings:
        return f"in a meeting in the {meetings[agent.id]}"
    loc = agent.location or {}
    kind = loc.get("kind")
    if kind == "room":
        return f"in the {loc.get('room')}"
    if kind == "item":
        return f"by the {loc.get('label')} in the {loc.get('room')}"
    if kind == "agent" and loc.get("agentId") in by_id:
        other = by_id[loc["agentId"]]
        return f"with {other.name} ({where_text(other, space, by_id, meetings)})" \
            if other.location is None or other.location.get("agentId") != agent.id \
            else f"with {other.name}"
    desk_room = space.desks.get(agent.id)
    desk = "your desk" if you else "their desk"
    if space.saved and not any(i.agent_id == agent.id for i in space.items()):
        return f"in the {desk_room}" if desk_room else "somewhere around"
    return f"at {desk}" + (f" in the {desk_room}" if desk_room else "")


# --- resolving a place someone asked for -------------------------------------------------


class PlaceError(ValueError):
    pass


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower().replace("'s", "")).strip()


def resolve_place(ref: str, space: Space, agents: list[Agent], me: Agent) -> dict[str, Any] | None:
    """Turn "the lounge", "Ada", "the coffee machine" or "my desk" into a location."""
    q = _norm(ref)
    q = re.sub(r"^(the|to the|to|over to|back to|near|next to|by the|by)\s+", "", q)
    if not q:
        raise PlaceError("say where to go")
    if q in ("desk", "my desk", "your desk", "seat", "my seat", "workstation"):
        return None
    for r in space.rooms:
        if _norm(r.name) == q or _norm(r.name).rstrip("s") == q.rstrip("s"):
            return {"kind": "room", "room": r.name}
    for a in agents:
        if a.id != me.id and (_norm(a.name) == q or _norm(a.name).split(" ")[0] == q):
            return {"kind": "agent", "agentId": a.id, "name": a.name}
    items = space.items()
    for pred in (lambda i: _norm(i.label) == q, lambda i: q in _norm(i.label),
                 lambda i: _norm(i.label) in q):
        hits = [i for i in items if pred(i)]
        if hits:
            it = hits[0]
            if it.agent_id == me.id:
                return None
            return {"kind": "item", "room": it.room, "itemId": it.id, "itemKind": it.kind,
                    "label": it.label}
    for r in space.rooms:
        if q in _norm(r.name) or _norm(r.name) in q:
            return {"kind": "room", "room": r.name}
    options = ", ".join(r.name for r in space.rooms)
    raise PlaceError(f"there is no '{ref}' here. Rooms: {options}; you can also go to a "
                     f"colleague, an item in a room, or back to your desk.")


# --- snapshots ------------------------------------------------------------------------------


@dataclass
class World:
    org: Org
    agents: list[Agent]
    by_id: dict[str, Agent]
    space: Space
    meetings: dict[str, str]
    objects: list[WorldObject]

    def room_of(self, agent: Agent) -> str:
        return room_of(agent, self.space, self.by_id, self.meetings)

    def where(self, agent: Agent, *, you: bool = False) -> str:
        return where_text(agent, self.space, self.by_id, self.meetings, you=you)

    def object_room(self, obj: WorldObject) -> str:
        if obj.holder_id and obj.holder_id in self.by_id:
            return self.room_of(self.by_id[obj.holder_id])
        place = obj.place or {}
        if place.get("room"):
            return str(place["room"])
        if place.get("agentId") in self.by_id:
            return self.space.desks.get(place["agentId"], OPEN_FLOOR)
        return OPEN_FLOOR

    def people_in(self, room: str, *, exclude: str | None = None) -> list[Agent]:
        return [a for a in self.agents
                if a.id != exclude and not a.is_supervisor and self.room_of(a) == room]


async def load_world(org_id: str) -> World:
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None:
            raise PlaceError("organization not found")
        agents = list((await session.execute(select(Agent).where(
            Agent.org_id == org_id, Agent.status != "disabled"))).scalars())
        objects = list((await session.execute(select(WorldObject).where(
            WorldObject.org_id == org_id).order_by(WorldObject.created_at))).scalars())
    space = await space_of(org, agents)
    return World(org, agents, {a.id: a for a in agents}, space, await _meeting_rooms(org_id),
                 objects)


def object_line(w: World, o: WorldObject) -> str:
    where = (f"held by {w.by_id[o.holder_id].name}" if o.holder_id in w.by_id
             else f"in the {w.object_room(o)}")
    extra = f" — {o.description}" if o.description else ""
    state = ", ".join(f"{k}: {v}" for k, v in (o.state or {}).items())
    return f"{o.name} ({where}){extra}{f' [{state}]' if state else ''}"


def describe(w: World, me: Agent) -> str:
    """What an agent perceives: where it is, who's around, the rooms and the objects."""
    here = w.room_of(me)
    lines = [f"You are {w.where(me, you=True)}."]
    company = w.people_in(here, exclude=me.id)
    lines.append("With you here: " + (", ".join(a.name for a in company) or "nobody") + ".")
    mine = [o for o in w.objects if o.holder_id == me.id]
    if mine:
        lines.append("You are holding: " + "; ".join(object_line(w, o) for o in mine) + ".")
    lines.append("\nRooms:")
    for r in w.space.rooms:
        notable = [i.label for i in r.items if i.kind not in CLUTTER][:10]
        lines.append(f"- {r.name}{' (you are here)' if r.name == here else ''}: "
                     + (", ".join(dict.fromkeys(notable)) or "empty"))
    lines.append("\nWhere everyone is:")
    for a in w.agents:
        if a.id != me.id and not a.is_supervisor:
            lines.append(f"- {a.name}: {w.where(a)}")
    loose = [o for o in w.objects if o.holder_id != me.id]
    if loose:
        lines.append("\nObjects:")
        lines += [f"- {object_line(w, o)}" for o in loose[:40]]
    return "\n".join(lines)


def brief(w: World, me: Agent) -> str:
    """One paragraph for the system prompt."""
    here = w.room_of(me)
    company = [a.name for a in w.people_in(here, exclude=me.id)]
    held = [o.name for o in w.objects if o.holder_id == me.id]
    rooms = ", ".join(r.name for r in w.space.rooms)
    text = (f"You have a body in a shared physical space (rooms: {rooms}). You are "
            f"{w.where(me, you=True)}"
            + (f", with {', '.join(company)}" if company else "")
            + (f", holding {', '.join(held)}" if held else "") + ".")
    return text


async def set_location(agent: Agent, location: dict[str, Any] | None, text: str) -> None:
    async with SessionLocal() as session:
        row = await session.get(Agent, agent.id)
        if row is None:
            return
        row.location = location
        await session.commit()
    agent.location = location
    await bus.publish("agent.moved", {"location": location, "text": text},
                      org_id=agent.org_id, agent_id=agent.id)


def world_enabled(org: Org) -> bool:
    return org.kind != "system" and ((org.settings or {}).get("world") or {}).get(
        "enabled", True) is not False


def world_rules(org: Org) -> str:
    return str(((org.settings or {}).get("world") or {}).get("rules") or "").strip()
