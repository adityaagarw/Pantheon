"""Life: what the residents of a living world need and want, a little like The Sims.

An organization can turn on "life" (``settings.life = {"enabled": true, "pace": 1}``).
Each agent then has needs that run down over time (energy, food, hygiene, social, fun,
comfort), a few traits, and wants of their own. They look after their needs by doing
things *somewhere*: sleeping needs a bed or a tent, eating a kitchen, a campfire or a
crop, washing a shower or a bath. If the world doesn't have one yet, they have to build
it, which is the point: homes, washrooms, gardens and forests appear because someone
wanted them.

Needs are stored with the time they were last set and worked out lazily, so nothing
ticks in the background.
"""

from __future__ import annotations

import math
import random
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.events import bus
from app.models import Agent, Org

# name: (what looks after it, hours to run out at pace 1)
NEEDS: dict[str, tuple[str, float]] = {
    "energy": ("sleep and rest", 16),
    "food": ("a meal", 7),
    "hygiene": ("a shower, a bath, a toilet", 12),
    "social": ("time with others", 9),
    "fun": ("play, nature, making things you love", 6),
    "comfort": ("a cosy place of your own", 12),
}
MAX_WANTS = 8

# What you can do, what it needs nearby (matched against item kinds and labels) and what it gives.
ACTIVITIES: dict[str, dict[str, Any]] = {
    "sleep": {
        "where": r"bed|tent|hammock|sleeping", "not": r"cabinet|bedroom|lamp",
        "gives": {"energy": 70, "comfort": 15},
        "verb": "sleeps", "you": "sleep",
        "build": "a bed (bedSingle, bedDouble) or a tent (nature:tent_detailedClosed)"},
    "rest": {
        "where": r"sofa|lounge|bench|armchair|chair|hammock|stump|log|cushion",
        "gives": {"energy": 20, "comfort": 25}, "verb": "rests", "you": "rest",
        "build": "a sofa, a bench or a chair"},
    "eat": {
        "where": r"fridge|stove|kitchen|microwave|toaster|coffee|campfire|crop|melon|pumpkin|"
                 r"carrot|turnip|vending|dining|table", "not": r"lamp|cabinet|bathroom",
        "gives": {"food": 60, "fun": 5}, "verb": "eats", "you": "eat",
        "build": "a kitchen (kitchenFridge, kitchenStove), a table or a campfire "
                 "(nature:campfire_logs)"},
    "wash": {
        "where": r"shower|bathtub|sink", "gives": {"hygiene": 70, "comfort": 5},
        "verb": "washes", "you": "wash",
        "build": "a shower or a bathtub (and walls around it, ideally)"},
    "toilet": {
        "where": r"toilet", "gives": {"hygiene": 30, "comfort": 10},
        "verb": "uses the toilet", "you": "use the toilet", "build": "a toilet, in a washroom"},
    "play": {
        "where": r"television|radio|speaker|game|arcade|guitar|piano|pool|canoe|swing|"
                 r"bear|books|bookcase|ball|slide",
        "gives": {"fun": 40}, "verb": "plays", "you": "play",
        "build": "something fun: a TV, books, a canoe, a game"},
    "nature": {
        "where": r"^nature:(tree|flower|plant|grass|ground_river|lily|rock|mushroom|cliff|stump|log)",
        "gives": {"fun": 25, "comfort": 10, "energy": 5}, "verb": "enjoys nature",
        "you": "enjoy nature", "build": "trees, flowers or a river (nature:...)"},
    "chat": {"people": True, "gives": {"social": 40, "fun": 10}, "verb": "chats", "you": "chat"},
}
EMOTES = {"chat": "wave", "play": "dance", "nature": "yes", "eat": "yes"}


class LifeError(ValueError):
    pass


def settings_of(org: Org) -> dict[str, Any]:
    return dict((org.settings or {}).get("life") or {})


def enabled(org: Org) -> bool:
    return org.kind != "system" and settings_of(org).get("enabled") is True


def _pace(org: Org | None, life: dict[str, Any]) -> float:
    pace = settings_of(org).get("pace") if org is not None else life.get("pace")
    try:
        return max(0.0, min(float(pace if pace is not None else 1.0), 10.0))
    except (TypeError, ValueError):
        return 1.0


def _at(life: dict[str, Any]) -> datetime:
    try:
        t = datetime.fromisoformat(str(life.get("at")))
        return t if t.tzinfo else t.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return utcnow()


def needs_now(life: dict[str, Any], org: Org | None = None, now: datetime | None = None) -> dict[str, int]:
    """The needs as they are now, after running down since they were last set."""
    hours = max(0.0, ((now or utcnow()) - _at(life)).total_seconds() / 3600)
    pace = _pace(org, life)
    stored = life.get("needs") or {}
    return {n: int(round(max(0.0, min(100.0, float(stored.get(n, 75)) - hours * pace * 100 / h))))
            for n, (_, h) in NEEDS.items()}


def mood(needs: dict[str, int]) -> str:
    avg = sum(needs.values()) / max(1, len(needs))
    low = min(needs.values()) if needs else 100
    if low < 10 or avg < 25:
        return "miserable"
    if low < 25 or avg < 45:
        return "low"
    if avg < 65:
        return "okay"
    return "good" if avg < 82 else "great"


def _feel(name: str, v: int) -> str:
    if v >= 30:
        return ""
    words = {"energy": "exhausted", "food": "hungry", "hygiene": "grubby", "social": "lonely",
             "fun": "bored", "comfort": "uncomfortable"}
    return ("very " if v < 12 else "") + words[name]


def new_life(spec: dict[str, Any] | None, seed: str = "") -> dict[str, Any]:
    """A fresh life for a new resident: rested and fed, with their traits and wants."""
    spec = spec or {}
    rnd = random.Random(seed)
    return {"needs": {n: rnd.randint(62, 92) for n in NEEDS}, "at": utcnow().isoformat(),
            "traits": [str(t)[:40] for t in (spec.get("traits") or [])][:6],
            "wants": [{"id": new_id("want")[-6:], "text": str(w)[:200], "since": utcnow().isoformat()}
                      for w in (spec.get("wants") or [])][:MAX_WANTS],
            "done": []}


def snapshot(life: dict[str, Any] | None, org: Org | None = None) -> dict[str, Any] | None:
    """What the UI shows: needs now, mood, traits, wants and recent achievements."""
    if not life or not life.get("needs"):
        return None
    needs = needs_now(life, org)
    return {"needs": needs, "mood": mood(needs), "traits": life.get("traits") or [],
            "wants": life.get("wants") or [], "done": (life.get("done") or [])[-5:],
            "doing": life.get("doing")}


def prompt_section(agent: Agent, org: Org) -> str:
    life = agent.life or {}
    if not life.get("needs"):
        return ""
    needs = needs_now(life, org)
    bars = " · ".join(f"{n} {v}" + (f" ({f})" if (f := _feel(n, v)) else "") for n, v in needs.items())
    lines = ["# How you feel", f"Mood: {mood(needs)}. {bars} (100 is fully satisfied)."]
    if traits := life.get("traits"):
        lines.append("Your nature: " + ", ".join(traits) + ".")
    wants = life.get("wants") or []
    lines.append("What you want: " + ("; ".join(w["text"] for w in wants) if wants
                                      else "nothing in particular yet. Think of something you'd love."))
    if done := (life.get("done") or [])[-3:]:
        lines.append("Lately you made happen: " + "; ".join(d["text"] for d in done) + ".")
    lines.append(LIFE_RULES)
    return "\n".join(lines)


LIFE_RULES = """\
You are a person living in this world, not an assistant waiting for orders. Needs run \
down as time passes; look after them with life_do (sleep, rest, eat, wash, toilet, play, \
nature, chat). Each needs the right thing nearby: a bed to sleep in, a kitchen, table or \
campfire to eat at, a shower or bath to wash in, someone to chat with. If the world \
doesn't have it, build it (a home with a bedroom, a washroom, a garden). Chase your wants: \
they are yours, and making them real feels good (life_want done). When you think of \
something new you'd love, add it. A low need comes first; otherwise do what you want."""


# --- where things are ----------------------------------------------------------------------


async def _labels(kinds: set[str]) -> dict[str, str]:
    from app.services import worldbuild as wb

    out: dict[str, str] = {}
    for k in kinds:
        try:
            _, info = await wb.kind_info(k)
            out[k] = f"{k} {info.get('label', '')}"
        except Exception:  # noqa: BLE001 - unknown kinds just don't match
            out[k] = k
    return out


def _pos(agent: Agent, design: dict[str, Any]) -> tuple[float, float]:
    loc = agent.location or {}
    items = design.get("items") or []
    if loc.get("itemId"):
        it = next((i for i in items if i.get("id") == loc["itemId"]), None)
        if it:
            return float(it["x"]), float(it["z"])
    if loc.get("room"):
        r = next((r for r in design.get("rooms") or [] if r.get("name") == loc["room"]), None)
        if r:
            return float(r["x"]) + float(r["w"]) / 2, float(r["z"]) + float(r["d"]) / 2
    desk = next((i for i in items if i.get("agentId") == agent.id), None)
    if desk:
        return float(desk["x"]), float(desk["z"])
    return float(design.get("width", 20)) / 2, float(design.get("depth", 20)) / 2


def room_at(design: dict[str, Any], x: float, z: float) -> str | None:
    for r in design.get("rooms") or []:
        if float(r["x"]) <= x <= float(r["x"]) + float(r["w"]) and \
                float(r["z"]) <= z <= float(r["z"]) + float(r["d"]):
            return str(r["name"])
    return None


async def find_spots(design: dict[str, Any], activity: str, me: Agent,
                     limit: int = 3) -> list[dict[str, Any]]:
    """The nearest things to do an activity at, your own first."""
    pattern = re.compile(ACTIVITIES[activity]["where"], re.I)
    skip = re.compile(ACTIVITIES[activity].get("not") or r"(?!)", re.I)
    items = design.get("items") or []
    labels = await _labels({str(i.get("kind")) for i in items})
    x0, z0 = _pos(me, design)
    hits = [i for i in items if pattern.search(text := labels.get(str(i.get("kind")), ""))
            and not skip.search(text)]
    hits.sort(key=lambda i: (i.get("by") != me.name, math.hypot(float(i["x"]) - x0, float(i["z"]) - z0)))
    return [{"id": i["id"], "kind": i["kind"],
             "label": labels[str(i["kind"])].split(" ", 1)[-1].strip() or i["kind"],
             "x": i["x"], "z": i["z"], "room": room_at(design, float(i["x"]), float(i["z"])),
             "mine": i.get("by") == me.name} for i in hits[:limit]]


# --- changing a life ---------------------------------------------------------------------------


async def _store(agent: Agent, org: Org, life: dict[str, Any]) -> None:
    from app.services.orgs import agent_to_dict

    life = {**life, "pace": _pace(org, life)}
    async with SessionLocal() as session:
        row = await session.get(Agent, agent.id)
        if row is None:
            return
        row.life = life
        await session.commit()
        await session.refresh(row)
        agent.life = life
        await bus.publish("agent.updated", agent_to_dict(row), org_id=row.org_id, agent_id=row.id)


def _apply(life: dict[str, Any], org: Org, gives: dict[str, float]) -> dict[str, Any]:
    needs = needs_now(life, org)
    for n, v in gives.items():
        if n in needs:
            needs[n] = int(max(0, min(100, needs[n] + v)))
    return {**life, "needs": needs, "at": utcnow().isoformat()}


async def adjust(agent: Agent, org: Org, gives: dict[str, float], *,
                 doing: str | None = None) -> dict[str, Any]:
    life = _apply(dict(agent.life or {}), org, gives)
    if doing is not None:
        life["doing"] = doing
    await _store(agent, org, life)
    return life


async def nudge(org_id: str, agent_name: str, gives: dict[str, float]) -> None:
    """A small boost from something done elsewhere (e.g. building gives a little fun)."""
    async with SessionLocal() as session:
        org = await session.get(Org, org_id)
        if org is None or not enabled(org):
            return
        agent = await session.scalar(select(Agent).where(Agent.org_id == org_id, Agent.name == agent_name))
    if agent is not None and (agent.life or {}).get("needs"):
        await adjust(agent, org, gives)


async def want(agent: Agent, org: Org, action: str, text: str = "", want_id: str = "") -> str:
    life = dict(agent.life or {})
    wants = list(life.get("wants") or [])
    if action == "add":
        text = text.strip()
        if not text:
            raise LifeError("say what you want")
        if len(wants) >= MAX_WANTS:
            raise LifeError(f"you already want {MAX_WANTS} things; finish or drop one first")
        wants.append({"id": new_id("want")[-6:], "text": text[:200], "since": utcnow().isoformat()})
        life["wants"] = wants
        await _store(agent, org, life)
        return f"You now want: {text[:200]}"
    ref = (want_id or text).strip().lower()
    hit = next((w for w in wants if w["id"] == ref or ref and ref in w["text"].lower()), None)
    if hit is None:
        raise LifeError("you don't have that want. Yours: "
                        + ("; ".join(f"[{w['id']}] {w['text']}" for w in wants) or "none"))
    wants.remove(hit)
    life["wants"] = wants
    if action == "done":
        life["done"] = [*(life.get("done") or []), {"text": hit["text"], "at": utcnow().isoformat()}][-20:]
        await _store(agent, org, _apply(life, org, {"fun": 25, "comfort": 15}))
        await bus.publish("agent.emote", {"emote": "dance", "at": ""}, org_id=org.id, agent_id=agent.id)
        return f"You made it happen: {hit['text']}. That feels good."
    await _store(agent, org, life)
    return f"You let go of: {hit['text']}"
