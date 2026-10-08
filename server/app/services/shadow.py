"""Shadowing: an agent learns a piece of work by watching the user do it.

The user records themselves in one of three places:

- the sandbox desktop: a small listener inside the desktop logs clicks, window
  changes and how much was typed (never the keys), and the backend takes a
  screenshot whenever something happens;
- their browser, with the Pantheon extension (``shadow/extension``): clicks with
  the element's label, pages, and form values (never passwords or card fields);
- their own computer, with ``shadow/recorder.py``: windows, clicks and
  screenshots (never keystrokes).

Every recording has a short code; the extension and the recorder post events
with it. When the user is done they ask the agent to learn from it: the agent
reviews the timeline and screenshots (``shadow_review``) and writes a playbook
(``save_playbook``) that it can follow later, on request or on a schedule.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hashlib
import json
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

SOURCES = ("sandbox", "browser", "desktop")
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
    from app.services.host import bridge

    on_host = s.source == "desktop" and s.status == "recording" and bridge.recording() == s.id
    return {"id": s.id, "orgId": s.org_id, "agentId": s.agent_id, "agentName": agent_name,
            "hostRecording": bridge.info.get("host") if on_host else None,
            "hostAvailable": s.source == "desktop" and bridge.can("recorder"),
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


async def create_session(org_id: str, agent_id: str, title: str, source: str,
                         note: str = "") -> ShadowSession:
    if source not in SOURCES:
        raise ShadowError(f"source must be one of {', '.join(SOURCES)}")
    if not title.strip():
        raise ShadowError("say what you're about to do (the title)")
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.org_id != org_id:
            raise ShadowError("no such agent in this organization")
        if source == "sandbox" and (await session.execute(select(ShadowSession.id).where(
                ShadowSession.source == "sandbox", ShadowSession.status == "recording"))).first():
            raise ShadowError("the sandbox desktop is already being recorded; stop that first")
        code = await _new_code(session)
        s = ShadowSession(org_id=org_id, agent_id=agent_id, title=title.strip()[:200],
                          note=note.strip(), source=source, code=code)
        session.add(s)
        await session.commit()
    await bus.publish("shadow.session", session_to_dict(s, agent.name), org_id=org_id)
    if source == "sandbox":
        _recorders[s.id] = asyncio.create_task(_record_sandbox(s.id, org_id), name=f"shadow-{s.id}")
    elif source == "desktop":
        await _host_record(s)
    return s


async def _host_record(s: ShadowSession) -> None:
    """With Pantheon Host running, "My computer" starts recording by itself (no code)."""
    from app.services.host import bridge
    from app.tools.base import ToolError

    if not bridge.can("recorder"):
        return
    try:
        await bridge.call("record_start", {"sessionId": s.id, "code": s.code, "title": s.title},
                          timeout=20)
    except ToolError as e:
        log.warning("shadow: Pantheon Host couldn't start recording: %s", e)


async def pending(source: str) -> ShadowSession | None:
    """The newest recording waiting for a recorder of this kind (the browser extension asks)."""
    async with SessionLocal() as session:
        return (await session.execute(select(ShadowSession).where(
            ShadowSession.source == source, ShadowSession.status == "recording")
            .order_by(ShadowSession.started_at.desc()))).scalars().first()


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


async def by_code(code: str) -> ShadowSession:
    async with SessionLocal() as session:
        s = (await session.execute(select(ShadowSession).where(
            ShadowSession.code == code.strip().upper(),
            ShadowSession.status == "recording"))).scalars().first()
    if s is None:
        raise ShadowError("no recording with that code is running (start one on the Shadow tab)")
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
    from app.services.host import bridge
    from app.tools.base import ToolError

    if bridge.recording() == session_id:
        try:
            await bridge.call("record_stop", {"sessionId": session_id}, timeout=15)
        except ToolError:
            pass  # it also stops by itself when its next upload is refused
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
    where = {"sandbox": "on the sandbox desktop", "browser": "in their browser",
             "desktop": "on their own computer"}[s.source]
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


# --- the sandbox desktop recorder ------------------------------------------------------------

_recorders: dict[str, asyncio.Task] = {}

# Runs inside the sandbox desktop (Python + pynput are part of the cua image). It logs
# clicks, window changes and how much was typed, never which keys.
LISTENER = r'''
import json, os, re, subprocess, sys, threading, time
from pynput import keyboard, mouse

NOISE = re.compile(r"\(\d+\)|\[\d+\]|\b\d{1,3}%|^\W+")  # spinners, counts, progress

base = "/tmp/pantheon-shadow"
os.makedirs(base, exist_ok=True)
if sys.argv[1] == "--spawn":  # detach: the computer-server waits for inherited sockets
    log = open(f"{base}/{sys.argv[2]}.log", "w")
    subprocess.Popen([sys.executable, __file__, sys.argv[2]], stdin=subprocess.DEVNULL,
                     stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    sys.exit(0)
sid = sys.argv[1]
path, stop = f"{base}/{sid}.jsonl", f"{base}/{sid}.stop"
t0, lock = time.time(), threading.Lock()
typed = {"n": 0, "special": [], "last": 0.0}
SPECIAL = {"enter", "tab", "esc", "backspace", "delete", "up", "down", "left", "right",
           "page_up", "page_down", "home", "end"}

def title():
    try:
        wid = subprocess.run(["xprop", "-root", "_NET_ACTIVE_WINDOW"], capture_output=True,
                             text=True, timeout=2).stdout.split("#")[-1].split(",")[0].strip()
        out = subprocess.run(["xprop", "-id", wid, "_NET_WM_NAME"], capture_output=True,
                             text=True, timeout=2).stdout
        return out.split("=", 1)[1].strip().strip('"') if "=" in out else ""
    except Exception:
        return ""

def emit(kind, **data):
    with lock, open(path, "a") as f:
        f.write(json.dumps({"t": round(time.time() - t0, 2), "kind": kind, "data": data}) + "\n")

def flush():
    if typed["n"] or typed["special"]:
        n, special = typed["n"], typed["special"][:20]
        typed.update(n=0, special=[])
        emit("typing", keys=n, special=special, window=title())

def on_click(x, y, button, pressed):
    if pressed:
        flush()
        emit("click", x=int(x), y=int(y), button=str(button).split(".")[-1], window=title())

def on_press(key):
    typed["last"] = time.time()
    if isinstance(key, keyboard.KeyCode):
        typed["n"] += 1
    else:
        name = str(key).split(".")[-1]
        if name in SPECIAL:
            typed["special"].append(name)
            if name == "enter":
                flush()

mouse.Listener(on_click=on_click).start()
keyboard.Listener(on_press=on_press).start()
last = None
while not os.path.exists(stop):
    time.sleep(1)
    w = title()
    if last is None or NOISE.sub("", w).strip() != NOISE.sub("", last).strip():
        emit("window", title=w)
        last = w
    if typed["n"] and time.time() - typed["last"] > 2:
        flush()
'''


async def _record_sandbox(session_id: str, org_id: str) -> None:
    from app.computer.cua import ComputerError
    from app.tools.computer import computer

    comp = computer()
    folder = "/tmp/pantheon-shadow"
    script = base64.b64encode(LISTENER.encode()).decode()
    py = "/opt/venv/bin/python3"
    try:
        # The listener detaches itself (--spawn): a shell "&" would inherit the computer-
        # server's sockets and keep this command from returning.
        await comp.cmd("run_command", timeout=30, command=(
            f"mkdir -p {folder} && echo {script} | base64 -d > {folder}/listener.py && "
            f"{py} {folder}/listener.py --spawn {session_id}"))
    except ComputerError as e:
        log.warning("shadow: couldn't start the sandbox listener: %s", e)
        await _note(session_id, f"Couldn't start the desktop listener ({e}); only screenshots "
                                "are being recorded.")
    offset, last_hash, last_shot = 0, "", 0.0
    loop = asyncio.get_running_loop()
    try:
        while True:
            await asyncio.sleep(2)
            items: list[dict[str, Any]] = []
            try:
                r = await comp.cmd("run_command", timeout=20,
                                   command=f"tail -c +{offset + 1} {folder}/{session_id}.jsonl "
                                           f"2>/dev/null")
                chunk = r.get("stdout") or ""
                done = chunk[:chunk.rfind("\n") + 1]  # only whole lines
                offset += len(done.encode())
                for line in done.splitlines():
                    with contextlib.suppress(ValueError):
                        items.append(json.loads(line))
                now = loop.time()
                if items or now - last_shot > 20:
                    shot = await comp.screenshot(quality=60)
                    digest = hashlib.sha1(shot.encode()).hexdigest()
                    if digest != last_hash or items:
                        t = items[-1]["t"] if items else (await _elapsed(session_id))
                        windows = [i["data"].get("window") or i["data"].get("title")
                                   for i in items if i.get("data")]
                        items.append({"t": t + 0.01, "kind": "screen", "image": shot,
                                      "data": {"window": next((w for w in reversed(windows) if w), "")}})
                        last_hash, last_shot = digest, now
            except ComputerError as e:
                log.info("shadow: sandbox poll failed: %s", e)
                continue
            if items:
                try:
                    await add_events(session_id, items)
                except ShadowError:
                    return
    finally:
        with contextlib.suppress(Exception):
            await comp.cmd("run_command", timeout=15, command=(
                f"touch {folder}/{session_id}.stop; pkill -f 'listener.py {session_id}' || true"))


async def _elapsed(session_id: str) -> float:
    s = await get_session(session_id)
    return max((utcnow() - s.started_at).total_seconds(), 0.0)


async def _note(session_id: str, text: str) -> None:
    with contextlib.suppress(ShadowError):
        await add_events(session_id, [{"t": await _elapsed(session_id), "kind": "note",
                                       "data": {"text": text}}])


async def resume_recorders() -> None:
    """After a restart, sandbox recordings that were running are stopped (their listener
    lost its connection); the user can start a new one."""
    async with SessionLocal() as session:
        ids = (await session.execute(select(ShadowSession.id).where(
            ShadowSession.source == "sandbox", ShadowSession.status == "recording"))).scalars().all()
    for sid in ids:
        await stop(sid)
