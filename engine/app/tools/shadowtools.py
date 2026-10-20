"""Tools for learning from the user: review a recording, keep and read playbooks."""

from __future__ import annotations

import base64

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Playbook
from app.services import shadow
from app.services.shadow import ShadowError
from app.tools.base import I, S, ToolContext, ToolError, ToolOutput, obj, tool

MAX_SHOTS = 6


def stool(name: str, description: str, params: dict, **kw):
    return tool(name, description, params, category="learning", **kw)


async def _mine(ctx: ToolContext, session_id: str):
    try:
        s = await shadow.get_session(session_id)
    except ShadowError as e:
        raise ToolError(str(e)) from None
    if s.org_id != ctx.org.id or s.agent_id != ctx.agent.id:
        raise ToolError("that recording was made for someone else")
    return s


@stool("shadow_review", "Go through a recording of the user doing their work: a timeline of "
       "what they clicked, typed into forms, and which pages and windows they used, with "
       "screenshots. Page through long recordings with offset.",
       obj({"session": S, "offset": I, "count": I}, ["session"]))
async def shadow_review(args: dict, ctx: ToolContext) -> ToolOutput:
    s = await _mine(ctx, args["session"])
    offset = max(int(args.get("offset") or 0), 0)
    count = min(max(int(args.get("count") or 40), 1), 120)
    evs = await shadow.events(s.id, offset, count)
    if not evs:
        return ToolOutput(f"No events from {offset} on (the recording has {s.event_count}).")
    lines = [f"Recording \"{s.title}\" ({s.source}), events {offset + 1}-{offset + len(evs)} of "
             f"{s.event_count}." + (f" The user's notes: {s.note}" if s.note else "")]
    shots = [e for e in evs if e.image]
    # Spread the screenshots across the page of events.
    step = max(len(shots) // MAX_SHOTS, 1)
    chosen = {e.id for e in shots[::step][:MAX_SHOTS]}
    images, n = [], 0
    folder = shadow.images_dir(s.id)
    for e in evs:
        line = shadow.timeline_line(e)
        if e.id in chosen and (folder / e.image).is_file():
            n += 1
            ext = "jpeg" if e.image.endswith(".jpg") else e.image.rsplit(".", 1)[-1]
            images.append(f"data:image/{ext};base64,"
                          + base64.b64encode((folder / e.image).read_bytes()).decode())
            line += f"  [screenshot {n}]"
        lines.append(line)
    more = s.event_count - (offset + len(evs))
    if more > 0:
        lines.append(f"…{more} more events: shadow_review with offset={offset + len(evs)}")
    return ToolOutput("\n".join(lines), images)


@stool("save_playbook", "Save how to do a piece of work, as numbered steps you can follow later "
       "(saving with the same title updates it). Mention the recording it came from, if any.",
       obj({"title": S, "content": S, "session": S}, ["title", "content"]))
async def save_playbook(args: dict, ctx: ToolContext) -> str:
    if args.get("session"):
        await _mine(ctx, args["session"])
    try:
        p = await shadow.save_playbook(ctx.org.id, ctx.agent.id, args["title"], args["content"],
                                       args.get("session") or None)
    except ShadowError as e:
        raise ToolError(str(e)) from None
    return f"Saved playbook \"{p.title}\"."


@stool("list_playbooks", "List the playbooks you have: work you've learned to do.", obj({}))
async def list_playbooks(args: dict, ctx: ToolContext) -> str:
    books = await shadow.playbooks(ctx.org.id, ctx.agent.id)
    if not books:
        return "You have no playbooks yet."
    return "\n".join(f"- {p.title}: {p.content.splitlines()[0][:120]}" for p in books)


@stool("read_playbook", "Read one of your playbooks in full.", obj({"title": S}, ["title"]))
async def read_playbook(args: dict, ctx: ToolContext) -> str:
    async with SessionLocal() as session:
        p = (await session.execute(select(Playbook).where(
            Playbook.agent_id == ctx.agent.id,
            func.lower(Playbook.title) == args["title"].strip().lower()))).scalars().first()
    if p is None:
        raise ToolError(f"no playbook titled '{args['title']}' (see list_playbooks)")
    return f"# {p.title}\n\n{p.content}"
