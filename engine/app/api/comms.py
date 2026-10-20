"""Channels, messages and the user's inbox."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException
from sqlalchemy import func, select, update

from app.core.db import SessionLocal
from app.models import Channel, Message, UserInboxItem
from app.services import attachments, comms, flows
from app.services.comms import Sender, channel_to_dict, message_to_dict

router = APIRouter(prefix="/api/v1", tags=["comms"])


@router.get("/orgs/{org_id}/channels")
async def list_channels(org_id: str, kind: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(Channel).where(Channel.org_id == org_id)
        if kind:
            stmt = stmt.where(Channel.kind == kind)
        rows = (await session.execute(stmt.order_by(Channel.created_at))).scalars().all()
        last = dict((await session.execute(
            select(Message.channel_id, func.max(Message.created_at)).where(
                Message.org_id == org_id).group_by(Message.channel_id))).all())
        counts = dict((await session.execute(
            select(Message.channel_id, func.count()).where(
                Message.org_id == org_id).group_by(Message.channel_id))).all())
    out = []
    for c in rows:
        d = channel_to_dict(c)
        d["lastMessageAt"] = last[c.id].isoformat() if last.get(c.id) else None
        d["messageCount"] = counts.get(c.id, 0)
        out.append(d)
    return out


@router.post("/orgs/{org_id}/channels", status_code=201)
async def create_channel(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ch = await comms.create_channel(org_id, body.get("name", ""), body.get("members") or [],
                                    topic=body.get("topic", ""),
                                    notify=body.get("notify", "all"))
    return channel_to_dict(ch)


@router.patch("/channels/{channel_id}")
async def update_channel(channel_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Members (``members`` to replace, or ``add``/``remove``), ``name``, ``topic``,
    ``notify`` and ``archived``. Archiving frees the name for a replacement channel."""
    async with SessionLocal() as session:
        ch = await session.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    try:
        updated, _ = await comms.update_channel(
            ch.org_id, ch.id, members=body.get("members"), add=body.get("add"),
            remove=body.get("remove"), name=body.get("name"), topic=body.get("topic"),
            notify=body.get("notify"),
            archived=bool(body["archived"]) if "archived" in body else None)
    except comms.CommsError as e:
        raise HTTPException(400, str(e)) from None
    return channel_to_dict(updated)


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: str) -> None:
    async with SessionLocal() as session:
        ch = await session.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    try:
        await comms.delete_channel(ch.org_id, ch.id)
    except comms.CommsError as e:
        raise HTTPException(400, str(e)) from None


@router.get("/channels/{channel_id}/messages")
async def channel_messages(channel_id: str, limit: int = 100,
                           before: str | None = None) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        ch = await session.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(404, "channel not found")
    msgs = await comms.channel_history(ch.org_id, ch.id, limit=min(limit, 500), before=before)
    return [message_to_dict(m) for m in msgs]


@router.post("/orgs/{org_id}/messages", status_code=201)
async def user_message(org_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The user speaks: ``{"to": agentId|name}`` for a DM or ``{"channel": ref}``."""
    content = str(body.get("content", "")).strip()
    files: list = []
    if body.get("attachments"):
        try:
            files = await attachments.resolve_ids(org_id, list(body["attachments"]))
        except attachments.AttachmentError as e:
            raise HTTPException(400, str(e)) from None
    if not content and files:
        content = f"(attached {len(files)} file{'s' if len(files) > 1 else ''})"
    if not content:
        raise HTTPException(400, "content is required")
    meta: dict[str, Any] | None = {"attachments": [attachments.brief(a) for a in files]} if files else None
    if body.get("steer"):
        # Read by the agent mid-turn if it's working (app/agents/steering.py).
        meta = {**(meta or {}), "steer": True}
    ch = None
    if body.get("channel"):
        async with SessionLocal() as session:
            ch = await comms.find_channel(session, org_id, body["channel"])
        if ch is None:
            raise HTTPException(404, "channel not found")
        if "user" not in ch.members and ch.kind == "channel":
            async with SessionLocal() as session:
                row = await session.get(Channel, ch.id)
                row.members = sorted({*row.members, "user"})
                await session.commit()
                ch = row
        to = [m for m in ch.members if m != "user"]
    elif body.get("to"):
        async with SessionLocal() as session:
            agent = await comms.resolve_agent(session, org_id, str(body["to"]))
        to = [agent.id] if agent else []
    else:
        raise HTTPException(400, "specify `to` (agent) or `channel`")
    # A flow-mode organization: what the user says is a run's input, not chat for one agent.
    flow = None if body.get("steer") else await flows.flow_for_chat(org_id, to)
    if ch is not None:
        msg = await comms.post(org_id, Sender.user(), content, channel=ch, meta=meta,
                               deliver=flow is None)
    else:
        msg = await comms.send_direct(org_id, Sender.user(), str(body["to"]), content, meta=meta,
                                      deliver=flow is None)
    if files:
        await attachments.link_to_message([a.id for a in files], org_id, msg.id)
    if flow is not None:
        await _start_flow(org_id, flow, msg, content, meta)
    return message_to_dict(msg)


async def _start_flow(org_id: str, flow: Any, msg: Message, content: str,
                      meta: dict[str, Any] | None) -> None:
    order = " → ".join(n["title"] for n in flows.in_order(flow.graph))
    async with SessionLocal() as session:
        ch = await session.get(Channel, msg.channel_id)
    if ch is not None:  # first, so it comes before the answer however fast that is
        await comms.post(org_id, Sender.system(), f"Running the flow '{flow.name}' with this: "
                         f"{order}. Each step's result goes to the next, and the answer comes "
                         "back here. Follow it in the Flows pane.",
                         channel=ch, kind="flow_run", deliver=False,
                         meta={"flowRun": {"flowId": flow.id, "status": "running"}})
    await flows.start(flow.id, content, origin={
        "channelId": msg.channel_id, "messageId": msg.id,
        "attachments": (meta or {}).get("attachments") or []})


@router.get("/orgs/{org_id}/messages")
async def org_messages(org_id: str, limit: int = 200, agent_id: str | None = None,
                       task_id: str | None = None,
                       before: str | None = None) -> list[dict[str, Any]]:
    """All messages (every channel, DM and notification) for transparency views."""
    async with SessionLocal() as session:
        stmt = select(Message).where(Message.org_id == org_id)
        if agent_id:
            stmt = stmt.where(Message.sender_id == agent_id)
        if task_id:
            stmt = stmt.where(Message.task_id == task_id)
        if before:
            ref = await session.get(Message, before)
            if ref is not None:
                stmt = stmt.where(Message.created_at < ref.created_at)
        rows = (await session.execute(stmt.order_by(Message.created_at.desc())
                                      .limit(min(limit, 1000)))).scalars().all()
    return [message_to_dict(m) for m in reversed(rows)]


@router.get("/orgs/{org_id}/inbox")
async def user_inbox(org_id: str, unread_only: bool = False) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        stmt = select(UserInboxItem).where(UserInboxItem.org_id == org_id)
        if unread_only:
            stmt = stmt.where(UserInboxItem.read.is_(False))
        rows = (await session.execute(stmt.order_by(UserInboxItem.created_at.desc())
                                      .limit(300))).scalars().all()
    return [{"id": r.id, "kind": r.kind, "refId": r.ref_id, "title": r.title, "read": r.read,
             "createdAt": r.created_at.isoformat() if r.created_at else None} for r in rows]


@router.post("/orgs/{org_id}/inbox/read")
async def mark_read(org_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    ids = body.get("ids")
    async with SessionLocal() as session:
        stmt = update(UserInboxItem).where(UserInboxItem.org_id == org_id)
        if ids:
            stmt = stmt.where(UserInboxItem.id.in_(ids))
        await session.execute(stmt.values(read=True))
        await session.commit()
    return {"ok": True}
