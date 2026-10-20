"""Shadowing: an agent learns a piece of work by watching the user do it.

The user records themselves on this computer (app/computer/recorder.py): clicks, window
changes and how much was typed (never which keys), with a screenshot whenever something
happens. One recording at a time.

When the user is done they ask the agent to learn from it: the agent reviews the timeline
and screenshots (``shadow_review``) and writes a playbook (``save_playbook``) that it can
follow later, on request or on a schedule.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import logging
import re
import secrets
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select

from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import utcnow
from app.events import bus
from app.models import Agent, Playbook, ShadowEvent, ShadowSession
from app.services import comms
from app.services import secrets as secrets_svc

log = logging.getLogger(__name__)

SOURCES = ("computer",)
# Recordings made with Pantheon 1 say where they were made.
WHERE = {"computer": "on their computer", "desktop": "on their own computer",
         "sandbox": "on the sandbox desktop", "browser": "in their browser"}
KINDS = ("click", "input", "submit", "navigate", "window", "typing", "screen", "note", "key")
MAX_IMAGE_BYTES = 4_000_000
MAX_EVENTS_PER_POST = 200
LEARNER_TOOLS = ("shadow_review", "save_playbook", "list_playbooks", "read_playbook")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class ShadowError(ValueError):
    pass


def images_dir(session_id: str) -> Path:
    return settings.data_path / "shadow" / session_id


def session_to_dict(s: ShadowSession, agent_name: str | None = None) -> dict[str, Any]:
    return {"id": s.id, "orgId": s.org_id, "agentId": s.agent_id, "agentName": agent_name,
            "title": s.title, "note": s.note, "source": s.source, "status": s.status,
            "code": s.code if s.status == "recording" else None, "eventCount": s.event_count,
            "startedAt": s.started_at.isoformat() if s.started_at else None,
            "endedAt": s.ended_at.isoformat() if s.ended_at else None}


def event_to_dict(e: ShadowEvent) -> dict[str, Any]:
    return {"id": e.id, "t": e.t, "kind": e.kind, "data": e.data,
            "image": f"/api/v1/shadow/{e.session_id}/images/{e.image}" if e.image else None}


def playbook_to_dict(p: Playbook) -> dict[str, Any]:
    return {"id": p.id, "orgId": p.org_id, "agentId": p.agent_id, "sessionId": p.session_id,
            "title": p.title, "content": p.content,
            "createdAt": p.created_at.isoformat() if p.created_at else None,
            "updatedAt": p.updated_at.isoformat() if p.updated_at else None}


# --- sessions -------------------------------------------------------------------------------


async def create_session(org_id: str, agent_id: str, title: str, source: str = "computer",
                         note: str = "") -> ShadowSession:
    source = source or "computer"
    if source not in SOURCES:
        raise ShadowError(f"source must be one of {', '.join(SOURCES)}")
    if not title.strip():
        raise ShadowError("say what you're about to do (the title)")
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.org_id != org_id:
            raise ShadowError("no such agent in this organization")
        if (await session.execute(select(ShadowSession.id).where(
                ShadowSession.status == "recording"))).first():
            raise ShadowError("this computer is already being recorded; stop that first")
        code = await _new_code(session)
        s = ShadowSession(org_id=org_id, agent_id=agent_id, title=title.strip()[:200],
                          note=note.strip(), source=source, code=code)
        session.add(s)
        await session.commit()
    await bus.publish("shadow.session", session_to_dict(s, agent.name), org_id=org_id)
    _recorders[s.id] = asyncio.create_task(_record(s.id), name=f"shadow-{s.id}")
    return s


async def _record(session_id: str) -> None:
    """This computer's recorder, its events added as they come; it ends when the recording
    is stopped (the task is cancelled) or can't go on."""
    from app.computer import recorder

    async def add(items: list[dict[str, Any]]) -> None:
        await add_events(session_id, items)

    try:
        await recorder.record(add)
    except ShadowError:
        return
    except Exception as e:  # noqa: BLE001 - say why, keep what was recorded
        log.warning("shadow: recording stopped: %s", e)
        await _note(session_id, f"Recording stopped: {e}")


async def _new_code(session) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        taken = (await session.execute(select(ShadowSession.id).where(
            ShadowSession.code == code, ShadowSession.status == "recording"))).first()
        if not taken:
            return code
    raise ShadowError("couldn't make a session code; try again")


async def get_session(session_id: str) -> ShadowSession:
    async with SessionLocal() as session:
        s = await session.get(ShadowSession, session_id)
    if s is None:
        raise ShadowError("recording not found")
    return s


async def list_sessions(org_id: str) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(
            select(ShadowSession, Agent.name).join(Agent, Agent.id == ShadowSession.agent_id)
            .where(ShadowSession.org_id == org_id)
            .order_by(ShadowSession.started_at.desc()))).all()
    return [session_to_dict(s, name) for s, name in rows]


async def events(session_id: str, offset: int = 0, limit: int = 500) -> list[ShadowEvent]:
    async with SessionLocal() as session:
        return list((await session.execute(
            select(ShadowEvent).where(ShadowEvent.session_id == session_id)
            .order_by(ShadowEvent.t, ShadowEvent.created_at).offset(offset).limit(limit))).scalars())


def _clean(org_id: str, value: Any) -> Any:
    """Mask the org's secret values out of anything recorded."""
    if isinstance(value, str):
        return secrets_svc.redact(org_id, value)[:2000]
    if isinstance(value, dict):
        return {str(k)[:60]: _clean(org_id, v) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return [_clean(org_id, v) for v in value[:40]]
    return value


def _save_image(session_id: str, event_id: str, data_url: str) -> str | None:
    m = re.match(r"data:image/(jpeg|jpg|png|webp);base64,(.+)", data_url or "", re.S)
    if not m:
        return None
    try:
        raw = base64.b64decode(m.group(2), validate=False)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        return None
    ext = {"jpg": "jpg", "jpeg": "jpg", "png": "png", "webp": "webp"}[m.group(1)]
    folder = images_dir(session_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{event_id}.{ext}"
    (folder / name).write_bytes(raw)
    return name


async def add_events(session_id: str, items: list[dict[str, Any]]) -> int:
    """Store events posted by a recorder. Returns how many were kept."""
    s = await get_session(session_id)
    if s.status != "recording":
        raise ShadowError("this recording has stopped")
    rows: list[ShadowEvent] = []
    for item in items[:MAX_EVENTS_PER_POST]:
        kind = str(item.get("kind", ""))
        if kind not in KINDS:
            continue
        e = ShadowEvent(session_id=s.id, kind=kind, data=_clean(s.org_id, item.get("data") or {}))
        try:
            e.t = max(float(item.get("t") or 0), 0.0)
        except (TypeError, ValueError):
            e.t = 0.0
        if item.get("image"):
            e.image = _save_image(s.id, e.id, str(item["image"]))
        rows.append(e)
    if not rows:
        return 0
    async with SessionLocal() as session:
        session.add_all(rows)
        await session.commit()
        count = (await session.execute(select(func.count()).select_from(ShadowEvent).where(
            ShadowEvent.session_id == s.id))).scalar_one()
        live = await session.get(ShadowSession, s.id)
        live.event_count = count
        await session.commit()
    await bus.publish("shadow.events", {"sessionId": s.id, "eventCount": count,
                                        "events": [event_to_dict(e) for e in rows[-5:]]},
                      org_id=s.org_id)
    return len(rows)


async def stop(session_id: str) -> ShadowSession:
    task = _recorders.pop(session_id, None)
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
    async with SessionLocal() as session:
        s = await session.get(ShadowSession, session_id)
        if s is None:
            raise ShadowError("recording not found")
        if s.status == "recording":
            s.status, s.ended_at = "stopped", utcnow()
            await session.commit()
        name = (await session.get(Agent, s.agent_id)).name
    await bus.publish("shadow.session", session_to_dict(s, name), org_id=s.org_id)
    return s


async def delete_session(session_id: str) -> None:
    s = await stop(session_id)
    async with SessionLocal() as session:
        await session.execute(delete(ShadowSession).where(ShadowSession.id == session_id))
        await session.commit()
    shutil.rmtree(images_dir(session_id), ignore_errors=True)
    await bus.publish("shadow.deleted", {"id": session_id}, org_id=s.org_id)


async def learn(session_id: str) -> ShadowSession:
    """Hand a finished recording to its agent, which reviews it and writes a playbook."""
    s = await stop(session_id)
    if s.event_count == 0:
        raise ShadowError("nothing was recorded")
    from app.services import orgs

    async with SessionLocal() as session:
        agent = await session.get(Agent, s.agent_id)
        live = await session.get(ShadowSession, s.id)
        live.status = "learning"
        await session.commit()
        s = live
    have = {t.get("name") for t in agent.tools or [] if isinstance(t, dict)}
    missing = [{"name": n, "approval": "auto"} for n in LEARNER_TOOLS if n not in have]
    if missing:
        await orgs.update_agent(agent.id, {"tools": [*agent.tools, *missing]})
    minutes = max(1, round(((s.ended_at or utcnow()) - s.started_at).total_seconds() / 60))
    where = WHERE.get(s.source, "on their computer")
    await comms.notify(
        s.org_id, [agent.id],
        f"The user recorded themselves doing some work {where} so you can learn to do it: "
        f"\"{s.title}\" ({s.event_count} events over about {minutes} min)."
        + (f"\nTheir notes: {s.note}" if s.note else "")
        + f"\n\n1. Go through the whole recording with shadow_review (session {s.id}): what "
          "they clicked, which pages and windows they used, what they typed into forms, and "
          "screenshots.\n2. Work out the goal, the inputs, the steps and decisions, and how they "
          "checked the result.\n3. Write it down with save_playbook: when to do it, what you "
          "need, numbered steps in terms of the tools you have, and how to verify the result. "
          "Say plainly which steps you can't do with your tools.\n4. Ask the user about anything "
          "the recording didn't make clear, then tell them the playbook is ready and offer to do "
          "the work now or on a schedule.",
        kind="shadow", meta={"shadowSessionId": s.id})
    await bus.publish("shadow.session", session_to_dict(s, agent.name), org_id=s.org_id)
    return s


def timeline_line(e: ShadowEvent) -> str:
    d = e.data or {}
    t = f"[{int(e.t // 60):02d}:{int(e.t % 60):02d}]"
    where = d.get("window") or d.get("title") or ""
    where = f" in \"{where}\"" if where else ""
    if e.kind == "click":
        target = d.get("label") or d.get("text") or ""
        what = f" on {d.get('tag', '')} \"{target}\"".replace("  ", " ") if target else ""
        pos = f" at ({d['x']}, {d['y']})" if "x" in d and "y" in d else ""
        return f"{t} click{what}{pos}{where}{' ' + d['url'] if d.get('url') else ''}"
    if e.kind == "input":
        field = d.get("label") or d.get("name") or "a field"
        return f"{t} filled \"{field}\" = {d.get('value', '')!r}{where}"
    if e.kind == "submit":
        label = f" \"{d['label']}\"" if d.get("label") else ""
        return f"{t} submitted a form{label}{where}"
    if e.kind == "navigate":
        return f"{t} opened {d.get('url', '')} \"{d.get('title', '')}\""
    if e.kind == "window":
        return f"{t} switched to window \"{d.get('title', '')}\""
    if e.kind == "typing":
        keys = ", ".join(d.get("special") or [])
        return f"{t} typed {d.get('keys', 0)} characters{' (then ' + keys + ')' if keys else ''}{where}"
    if e.kind == "key":
        return f"{t} pressed {d.get('key', '')}{where}"
    if e.kind == "note":
        return f"{t} note from the user: {d.get('text', '')}"
    return f"{t} screen{where}"


# --- playbooks ------------------------------------------------------------------------------


async def save_playbook(org_id: str, agent_id: str, title: str, content: str,
                        session_id: str | None = None) -> Playbook:
    title, content = title.strip()[:200], content.strip()
    if not title or not content:
        raise ShadowError("a playbook needs a title and steps")
    content = secrets_svc.redact(org_id, content)
    async with SessionLocal() as session:
        p = (await session.execute(select(Playbook).where(
            Playbook.agent_id == agent_id, func.lower(Playbook.title) == title.lower()))
        ).scalars().first()
        if p is None:
            p = Playbook(org_id=org_id, agent_id=agent_id, title=title, content=content,
                         session_id=session_id)
            session.add(p)
        else:
            p.content, p.updated_at = content, utcnow()
            p.session_id = session_id or p.session_id
        if session_id:
            s = await session.get(ShadowSession, session_id)
            if s is not None and s.agent_id == agent_id:
                s.status = "learned"
        await session.commit()
    from app.services import memory

    first = content.splitlines()[0][:200] if content else ""
    await memory.add(org_id, f"I have a playbook \"{title}\" (learned by shadowing the user): "
                     f"{first} — read_playbook gives the full steps.", agent_id=agent_id,
                     kind="playbook")
    await bus.publish("playbook.saved", playbook_to_dict(p), org_id=org_id, agent_id=agent_id)
    return p


async def playbooks(org_id: str, agent_id: str | None = None) -> list[Playbook]:
    async with SessionLocal() as session:
        q = select(Playbook).where(Playbook.org_id == org_id)
        if agent_id:
            q = q.where(Playbook.agent_id == agent_id)
        return list((await session.execute(q.order_by(Playbook.title))).scalars())


async def update_playbook(playbook_id: str, patch: dict[str, Any]) -> Playbook:
    async with SessionLocal() as session:
        p = await session.get(Playbook, playbook_id)
        if p is None:
            raise ShadowError("playbook not found")
        if "title" in patch and str(patch["title"]).strip():
            p.title = str(patch["title"]).strip()[:200]
        if "content" in patch:
            p.content = secrets_svc.redact(p.org_id, str(patch["content"]))
        p.updated_at = utcnow()
        await session.commit()
    await bus.publish("playbook.saved", playbook_to_dict(p), org_id=p.org_id, agent_id=p.agent_id)
    return p


async def delete_playbook(playbook_id: str) -> None:
    async with SessionLocal() as session:
        p = await session.get(Playbook, playbook_id)
        if p is None:
            return
        await session.delete(p)
        await session.commit()
    await bus.publish("playbook.deleted", {"id": playbook_id}, org_id=p.org_id)


# --- recording on this computer --------------------------------------------------------------

_recorders: dict[str, asyncio.Task] = {}


async def _elapsed(session_id: str) -> float:
    s = await get_session(session_id)
    return max((utcnow() - s.started_at).total_seconds(), 0.0)


async def _note(session_id: str, text: str) -> None:
    with contextlib.suppress(ShadowError):
        await add_events(session_id, [{"t": await _elapsed(session_id), "kind": "note",
                                       "data": {"text": text}}])


async def resume_recorders() -> None:
    """After a restart, recordings that were running are stopped (their recorder went with
    the engine); the user can start a new one."""
    async with SessionLocal() as session:
        ids = (await session.execute(select(ShadowSession.id).where(
            ShadowSession.status == "recording"))).scalars().all()
    for sid in ids:
        await stop(sid)
