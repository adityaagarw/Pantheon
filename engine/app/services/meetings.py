"""Meetings: bounded, synchronous round-table discussions.

A meeting has a facilitator (an agent), participants, an agenda and settings:

- ``style``         discussion | decision | brainstorm | standup | review
- ``rounds``        how many times everyone speaks (bounded by the org's max)
- ``detail``        brief | detailed — how long each contribution may be
- ``create_tasks``  turn agreed action items into tasks assigned to their owners
- ``room``          which office room it happens in (3D placement)

Each round every participant speaks once (a focused LLM call with their
persona and the transcript); then the facilitator writes minutes. Minutes go
to shared memory, the meeting channel and every participant's inbox.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.agents import llm
from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.llm.providers import resolve_binding, resolve_for_agent
from app.models import Agent, Channel, Meeting, Org
from app.services import comms, memory, tasks
from app.services.comms import Sender

STYLES: dict[str, str] = {
    "discussion": "Open discussion: share information, perspectives and concerns.",
    "decision": ("Decision meeting: converge on a clear decision. In the final round state your "
                 "position explicitly (agree / disagree and why)."),
    "brainstorm": ("Brainstorm: propose many distinct ideas; build on others'. Don't criticize in "
                   "the first round."),
    "standup": ("Standup: say what you finished, what you're doing next, and any blockers. One "
                "round, very short."),
    "review": ("Review: critique the work under review concretely — what works, what must change, "
               "and why."),
}
DEFAULTS = {"max_participants": 8, "max_rounds": 3, "default_rounds": 2,
            "default_style": "discussion", "create_tasks": False}


class MeetingError(ValueError):
    pass


@dataclass
class MeetingRequest:
    org: Org
    facilitator: Agent
    participants: list[Agent]
    agenda: str
    style: str = "discussion"
    rounds: int = 2
    detail: str = "brief"
    create_tasks: bool = False
    room: str | None = None
    turn_id: str | None = None
    depth: int = 0
    called_by: str = "agent"
    extra: dict[str, Any] = field(default_factory=dict)


def meeting_to_dict(m: Meeting) -> dict[str, Any]:
    return {"id": m.id, "orgId": m.org_id, "channelId": m.channel_id,
            "facilitatorId": m.facilitator_id, "participants": m.participants, "agenda": m.agenda,
            "style": m.style, "options": m.options or {}, "status": m.status,
            "minutes": m.minutes, "taskIds": m.task_ids or [],
            "createdAt": m.created_at.isoformat() if m.created_at else None,
            "endedAt": m.ended_at.isoformat() if m.ended_at else None}


def settings_for(org: Org) -> dict[str, Any]:
    return {**DEFAULTS, **((org.settings or {}).get("meetings") or {})}


async def prepare(org: Org, facilitator: Agent, participant_refs: list[str], agenda: str,
                  *, style: str | None = None, rounds: int | None = None,
                  detail: str = "brief", create_tasks: bool | None = None,
                  room: str | None = None, turn_id: str | None = None, depth: int = 0,
                  called_by: str = "agent") -> MeetingRequest:
    cfg = settings_for(org)
    style = style or cfg["default_style"]
    if style not in STYLES:
        raise MeetingError(f"style must be one of {', '.join(STYLES)}")
    if detail not in ("brief", "detailed"):
        raise MeetingError("detail must be brief or detailed")
    agenda = (agenda or "").strip()
    if not agenda:
        raise MeetingError("an agenda is required")
    async with SessionLocal() as session:
        people: list[Agent] = []
        for ref in participant_refs:
            a = await comms.resolve_agent(session, org.id, ref)
            if a is None:
                raise MeetingError(f"no colleague named '{ref}'")
            if a.id != facilitator.id and a not in people:
                people.append(a)
    if not people:
        raise MeetingError("invite at least one colleague")
    if len(people) + 1 > cfg["max_participants"]:
        raise MeetingError(f"meetings are limited to {cfg['max_participants']} people in this org")
    rounds = 1 if style == "standup" else int(rounds or cfg["default_rounds"])
    rounds = max(1, min(rounds, cfg["max_rounds"]))
    return MeetingRequest(
        org=org, facilitator=facilitator, participants=people, agenda=agenda, style=style,
        rounds=rounds, detail=detail,
        create_tasks=cfg["create_tasks"] if create_tasks is None else bool(create_tasks),
        room=room, turn_id=turn_id, depth=depth, called_by=called_by,
    )


async def end_meeting(meeting_id: str, reason: str = "ended by the user") -> Meeting | None:
    """Stop treating a meeting as in progress (frees its room and participants). Returns the
    meeting, or None if it wasn't running."""
    async with SessionLocal() as session:
        row = await session.get(Meeting, meeting_id)
        if row is None or row.status != "running":
            return None
        row.status, row.ended_at = "failed", utcnow()
        row.minutes = row.minutes or f"(The meeting did not finish: {reason}.)"
        await session.commit()
    await bus.publish("meeting.ended", {"meetingId": row.id, "status": "failed",
                                        "reason": reason},
                      org_id=row.org_id, agent_id=row.facilitator_id)
    return row


async def sweep_interrupted() -> int:
    """At startup nothing can still be running: close meetings a restart cut short."""
    async with SessionLocal() as session:
        ids = (await session.execute(select(Meeting.id).where(
            Meeting.status == "running"))).scalars().all()
    for mid in ids:
        await end_meeting(mid, "the server restarted while it was in progress")
    return len(ids)


async def running_meetings(org_id: str) -> list[Meeting]:
    async with SessionLocal() as session:
        return list((await session.execute(select(Meeting).where(
            Meeting.org_id == org_id, Meeting.status == "running")
            .order_by(Meeting.created_at))).scalars().all())


async def run(req: MeetingRequest) -> dict[str, Any]:
    """Run the meeting to completion. Returns {meeting, transcript, minutes, tasks}."""
    org = req.org
    everyone = [req.facilitator, *req.participants]
    options = {"rounds": req.rounds, "detail": req.detail, "create_tasks": req.create_tasks,
               "room": req.room, "called_by": req.called_by}
    async with SessionLocal() as session:
        mtg = Meeting(org_id=org.id, facilitator_id=req.facilitator.id,
                      participants=[a.id for a in everyone], agenda=req.agenda, style=req.style,
                      options=options)
        session.add(mtg)
        await session.flush()
        ch = Channel(org_id=org.id, kind="meeting", key=f"meeting:{mtg.id}", name=req.agenda[:80],
                     topic=req.agenda, members=[a.id for a in everyone],
                     created_by=req.facilitator.id)
        session.add(ch)
        await session.flush()
        mtg.channel_id = ch.id
        await session.commit()
    await bus.publish("meeting.started", {
        "meetingId": mtg.id, "channelId": ch.id, "agenda": req.agenda, "style": req.style,
        "room": req.room, "facilitatorId": req.facilitator.id,
        "participants": [a.id for a in everyone]}, org_id=org.id, agent_id=req.facilitator.id)

    transcript: list[str] = []
    created: list[str] = []
    try:
        for rnd in range(1, req.rounds + 1):
            for a in everyone:
                said = await _speak(req, a, everyone, transcript, rnd)
                if not said or said.strip().lower().rstrip(".") == "pass":
                    continue
                transcript.append(f"{a.name}: {said}")
                await comms.post(org.id, Sender("agent", a.id, turn_id=req.turn_id,
                                                depth=req.depth),
                                 said, channel=ch, kind="meeting", deliver=False,
                                 meta={"meetingId": mtg.id, "round": rnd})
                await bus.publish("meeting.turn", {"meetingId": mtg.id, "speakerId": a.id,
                                                   "text": said[:1000], "round": rnd},
                                  org_id=org.id, agent_id=a.id)
        minutes, items = await _minutes(req, everyone, transcript)
    except llm.LLMFailure as e:
        await end_meeting(mtg.id, f"the model failed: {e}")
        raise MeetingError(f"the meeting was interrupted: {e}") from None
    except BaseException as e:  # noqa: BLE001 - incl. cancellation: never leave it "running"
        reason = "it was cancelled" if isinstance(e, asyncio.CancelledError) else \
            f"{type(e).__name__}: {e}"
        await asyncio.shield(end_meeting(mtg.id, reason))
        raise

    if req.create_tasks:
        created = await _create_action_tasks(req, everyone, items, mtg.id)

    # Record the minutes before marking the meeting done: "done" means everything is saved.
    await memory.add(org.id, f"Meeting minutes ({req.style}) — {req.agenda}\n{minutes}",
                     agent_id=None, kind="meeting")
    async with SessionLocal() as session:
        row = await session.get(Meeting, mtg.id)
        if row:
            row.status, row.minutes, row.ended_at, row.task_ids = "done", minutes, utcnow(), created
            await session.commit()
    await comms.post(org.id, Sender("agent", req.facilitator.id, turn_id=req.turn_id,
                                    depth=req.depth),
                     f"Minutes\n{minutes}", channel=ch, kind="meeting", deliver=False,
                     meta={"meetingId": mtg.id, "minutes": True})
    task_note = f"\n\nTasks created from action items: {', '.join(created)}" if created else ""
    recipients = [a.id for a in everyone if a.id != req.facilitator.id or req.called_by == "user"]
    await comms.notify(org.id, recipients,
                       f"Minutes of the {req.style} meeting \"{req.agenda}\" "
                       f"(facilitated by {req.facilitator.name}):\n{minutes}{task_note}\n\n"
                       "Act on any action items assigned to you.",
                       depth=req.depth + 1, kind="meeting", meta={"meetingId": mtg.id})
    await bus.publish("meeting.ended", {"meetingId": mtg.id, "status": "done",
                                        "minutes": minutes[:4000], "taskIds": created},
                      org_id=org.id, agent_id=req.facilitator.id)
    return {"meetingId": mtg.id, "transcript": transcript, "minutes": minutes, "tasks": created}


async def _model_for(req: MeetingRequest, agent: Agent):
    override = settings_for(req.org).get("model") or {}
    if override.get("provider_id") or override.get("model"):
        return await resolve_binding(override, agent_id=agent.id)
    return await resolve_for_agent(agent, req.org)


async def _speak(req: MeetingRequest, a: Agent, everyone: list[Agent], transcript: list[str],
                 rnd: int) -> str:
    model = await _model_for(req, a)
    others = ", ".join(f"{p.name} ({p.role})" for p in everyone if p.id != a.id)
    length = "1-3 sentences" if req.detail == "brief" else "a short paragraph (up to ~120 words)"
    system = (
        f"You are {a.name}, {a.role} at {req.org.name}.\n{a.persona.strip()}\n\n"
        f"You are in a meeting facilitated by {req.facilitator.name}. Also present: {others}.\n"
        f"{STYLES[req.style]}\n"
        f"Speak as yourself in {length}. Be concrete; don't repeat what was already said. "
        "If you have nothing to add, reply exactly: pass"
    )
    so_far = "\n".join(transcript) or "(nobody has spoken yet)"
    user = (f"Agenda: {req.agenda}\nRound {rnd} of {req.rounds}.\n\nTranscript so far:\n{so_far}"
            f"\n\nYour turn, {a.name}.")
    await bus.publish("agent.status", {"status": "working", "detail": "in a meeting"},
                      org_id=req.org.id, agent_id=a.id, persist=False)
    ai, _ = await llm.invoke(model, [SystemMessage(content=system), HumanMessage(content=user)],
                             org_id=req.org.id, agent_id=a.id, turn_id=req.turn_id,
                             purpose="meeting", stream=False)
    return llm.text_of(ai).strip()


async def _minutes(req: MeetingRequest, everyone: list[Agent],
                   transcript: list[str]) -> tuple[str, list[dict[str, str]]]:
    model = await _model_for(req, req.facilitator)
    names = ", ".join(a.name for a in everyone)
    system = (
        f"You are {req.facilitator.name}, who facilitated this {req.style} meeting. Write concise "
        "minutes in markdown with the sections 'Decisions' and 'Action items' (bullets as "
        "'Owner — action'). Only include what was actually agreed.\n"
        "After the minutes, output a line containing only ACTION_ITEMS_JSON followed by a JSON "
        'array like [{"owner": "<attendee name>", "title": "<short task title>", '
        '"details": "<what exactly to do>"}] — use [] if there are none. Owners must be attendees: '
        f"{names}."
    )
    user = f"Agenda: {req.agenda}\nAttendees: {names}\n\n" + "\n".join(transcript)
    ai, _ = await llm.invoke(model, [SystemMessage(content=system), HumanMessage(content=user)],
                             org_id=req.org.id, agent_id=req.facilitator.id, turn_id=req.turn_id,
                             purpose="meeting", stream=False)
    text = llm.text_of(ai).strip()
    minutes, _, tail = text.partition("ACTION_ITEMS_JSON")
    items: list[dict[str, str]] = []
    m = re.search(r"\[.*\]", tail, re.S)
    if m:
        try:
            raw = json.loads(m.group(0))
            items = [i for i in raw if isinstance(i, dict) and i.get("owner") and i.get("title")]
        except (json.JSONDecodeError, TypeError):
            items = []
    return (minutes.strip() or "(no minutes)"), items


async def _create_action_tasks(req: MeetingRequest, everyone: list[Agent],
                               items: list[dict[str, str]], meeting_id: str) -> list[str]:
    by_name = {a.name.lower(): a for a in everyone}
    refs: list[str] = []
    facilitator = Sender("agent", req.facilitator.id, turn_id=req.turn_id, depth=req.depth)
    for it in items[:12]:
        owner = by_name.get(str(it["owner"]).strip().lower())
        if owner is None:
            continue
        try:
            t = await tasks.create_task(
                req.org.id, facilitator, title=str(it["title"])[:200],
                description=f"{it.get('details', '')}\n\n(Action item from the meeting "
                            f"\"{req.agenda}\" — {meeting_id})",
                assignee=owner.id, labels=["meeting"],
            )
            refs.append(tasks.task_ref(t))
        except tasks.TaskError:
            continue  # e.g. facilitator lacks permission to assign this person
    return refs
