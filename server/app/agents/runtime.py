"""Agent runtime: a durable actor scheduler over LangGraph threads.

Lifecycle of work:
1. A message creates ``deliveries`` rows (one per recipient) and wakes the
   recipient.
2. The dispatcher starts a worker for any agent with due deliveries or an
   unfinished turn (bounded by a global concurrency semaphore; one worker
   per agent, ever).
3. The worker claims the deliveries (``FOR UPDATE SKIP LOCKED``), appends them
   to the agent's thread in ONE checkpoint, marks them done, then runs the
   graph to quiescence.
4. Failures never lose work: the thread keeps its checkpoint and the turn is
   resumed after an exponential backoff; after ``max_turn_failures`` the agent
   parks in ``error`` and the user is told (retry from the UI).

Circuit breakers: per-agent turns/hour, per-org daily token budget, and the
causal message depth limit (enforced at delivery time).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command
from sqlalchemy import func, select, text, update

from app.agents import signals
from app.agents.checkpointer import SqlCheckpointSaver
from app.agents.graph import COMPACT_AT, build_graph, thread_config
from app.agents.llm import text_of
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.events import bus
from app.llm.providers import resolve_for_agent
from app.models import (
    Agent,
    Approval,
    Channel,
    Delivery,
    Message,
    Org,
    Turn,
    UserInboxItem,
)
from app.services import comms
from app.services.comms import USER, Sender

log = logging.getLogger(__name__)

MAX_BATCH = 50
TURNS_PER_ACTIVATION = 3  # yield to other agents after this many turns
DEFAULT_TURNS_PER_HOUR = 120
BACKOFF = [5, 20, 60, 180, 600]


class RuntimeBusy(RuntimeError):
    """A control action that needs the agent idle was asked while it works."""


class Runtime:
    def __init__(self) -> None:
        self.saver = SqlCheckpointSaver(SessionLocal)
        self.graph = build_graph(self.saver)
        self._wake = asyncio.Event()
        self._ready: set[str] = set()
        self._workers: dict[str, asyncio.Task] = {}
        self._sem = asyncio.Semaphore(settings.max_concurrent_turns)
        self._dispatcher: asyncio.Task | None = None
        self._stop_requested: set[str] = set()
        self._resume_values: dict[str, dict[str, Any]] = {}
        self._inbox_images: list[str] = []
        self.started = False

    # --- lifecycle --------------------------------------------------------------

    async def start(self) -> None:
        signals.set_waker(self.wake)
        await self._recover()
        self._dispatcher = asyncio.create_task(self._dispatch_loop(), name="pantheon-dispatcher")
        self.started = True

    async def stop(self) -> None:
        signals.set_waker(None)
        if self._dispatcher:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
        workers = list(self._workers.values())
        for w in workers:
            w.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self.started = False

    def wake(self, agent_id: str) -> None:
        self._ready.add(agent_id)
        self._wake.set()

    async def _recover(self) -> None:
        """After a restart: release claims and resume interrupted turns."""
        async with SessionLocal() as session:
            await session.execute(update(Delivery).where(Delivery.status == "claimed")
                                  .values(status="pending", lease_until=None))
            rows = (await session.execute(select(Turn.agent_id).where(
                Turn.status == "running"))).scalars().all()
            await session.execute(update(Agent).where(Agent.runtime_status == "working")
                                  .values(runtime_status="idle", runtime_detail=""))
            await session.commit()
        for agent_id in set(rows):
            self._ready.add(agent_id)
        if rows:
            log.info("recovering %d interrupted turns", len(rows))

    async def _dispatch_loop(self) -> None:
        while True:
            try:
                try:
                    await asyncio.wait_for(self._wake.wait(), settings.dispatch_poll_seconds)
                except TimeoutError:
                    pass
                self._wake.clear()
                candidates = set(self._ready)
                self._ready.clear()
                candidates |= await self._due_agents()
                await self._sweep_leases()
                for agent_id in candidates:
                    if agent_id not in self._workers:
                        self._workers[agent_id] = asyncio.create_task(
                            self._worker(agent_id), name=f"agent:{agent_id}")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the dispatcher must never die
                log.exception("dispatcher iteration failed")
                await asyncio.sleep(1)

    async def _due_agents(self) -> set[str]:
        async with SessionLocal() as session:
            rows = await session.execute(text("""
                SELECT DISTINCT a.id FROM agents a
                JOIN orgs o ON o.id = a.org_id
                LEFT JOIN deliveries d ON d.agent_id = a.id AND d.status = 'pending'
                     AND d.available_at <= now()
                WHERE a.status = 'active' AND o.status = 'running'
                  AND a.runtime_status NOT IN ('awaiting_approval', 'error')
                  AND (a.retry_at IS NULL OR a.retry_at <= now())
                  AND (d.id IS NOT NULL OR (a.runtime_status IN ('retrying', 'cooling_down')))
            """))
            return {r[0] for r in rows}

    async def _sweep_leases(self) -> None:
        async with SessionLocal() as session:
            await session.execute(update(Delivery).where(
                Delivery.status == "claimed", Delivery.lease_until < func.now(),
            ).values(status="pending", lease_until=None))
            await session.commit()

    async def _worker(self, agent_id: str) -> None:
        try:
            async with self._sem:
                for _ in range(TURNS_PER_ACTIVATION):
                    if not await self._activate(agent_id):
                        break
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("worker for %s crashed", agent_id)
        finally:
            self._workers.pop(agent_id, None)
        # More may have arrived while we were busy.
        if await self._has_pending(agent_id):
            self.wake(agent_id)

    async def _has_pending(self, agent_id: str) -> bool:
        async with SessionLocal() as session:
            return bool(await session.scalar(select(func.count()).select_from(Delivery).where(
                Delivery.agent_id == agent_id, Delivery.status == "pending",
                Delivery.available_at <= func.now())))

    # --- one activation ----------------------------------------------------------

    async def _activate(self, agent_id: str) -> bool:
        """Run at most one turn. Returns True if it did work (and may do more)."""
        async with SessionLocal() as session:
            agent = await session.get(Agent, agent_id)
            org = await session.get(Org, agent.org_id) if agent else None
        if agent is None or org is None:
            return False
        if agent.status != "active" or org.status != "running":
            return False
        if agent.runtime_status in ("awaiting_approval", "error"):
            return False
        now = utcnow()
        if agent.retry_at and agent.retry_at > now:
            return False
        if not await self._within_limits(agent, org):
            return False

        cfg = thread_config(agent_id)
        snapshot = await self.graph.aget_state(cfg)
        if snapshot.next:
            interrupts = _interrupts(snapshot)
            if interrupts:
                await self._park_for_approval(agent, org, snapshot, interrupts)
                return False
            turn_id = (snapshot.values or {}).get("turn_id")
            await self._run(agent, org, turn_id, resume=True)
            return True

        claimed = await self._claim(agent_id)
        if not claimed:
            if agent.runtime_status != "idle":
                await self._set_status(agent, "idle", "")
            return False
        seen = set((snapshot.values or {}).get("seen") or [])
        fresh = [m for m in claimed if m.id not in seen]
        turn_id = new_id("trn")
        if fresh:
            content, depth, direct = await self._format_inbox(agent, fresh)
            async with SessionLocal() as session:
                session.add(Turn(id=turn_id, org_id=org.id, agent_id=agent_id,
                                 trigger_message_ids=[m.id for m in fresh]))
                await session.commit()
            pictures = await self._inbox_image_message(agent, org, turn_id)
            await self.graph.aupdate_state(cfg, {
                "messages": [HumanMessage(content=content, id=f"in_{turn_id}"), *pictures],
                "seen": [m.id for m in fresh],
                "turn_id": turn_id,
                "steps": 0,
                "depth": depth,
                "decisions": {"__reset__": True},
                "direct_senders": direct,
            }, as_node="inbox")
        await self._mark_done([m.id for m in claimed], agent_id, turn_id if fresh else None)
        if not fresh:
            return True
        await bus.publish("turn.started", {"turnId": turn_id,
                                           "messageIds": [m.id for m in fresh]},
                          org_id=org.id, agent_id=agent_id)
        await self._run(agent, org, turn_id, resume=False)
        return True

    async def _within_limits(self, agent: Agent, org: Org) -> bool:
        per_hour = int((agent.limits or {}).get("max_turns_per_hour") or DEFAULT_TURNS_PER_HOUR)
        since = utcnow() - timedelta(hours=1)
        async with SessionLocal() as session:
            count, oldest = (await session.execute(
                select(func.count(), func.min(Turn.started_at)).where(
                    Turn.agent_id == agent.id, Turn.started_at >= since))).one()
            budget = int((org.settings or {}).get("daily_token_budget") or 0)
            used = 0
            if budget:
                day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                used = int(await session.scalar(select(
                    func.coalesce(func.sum(Turn.input_tokens + Turn.output_tokens), 0)).where(
                    Turn.org_id == org.id, Turn.started_at >= day)) or 0)
        if budget and used >= budget:
            async with SessionLocal() as session:
                o = await session.get(Org, org.id)
                if o and o.status == "running":
                    o.status = "paused"
                    session.add(UserInboxItem(org_id=org.id, kind="error", ref_id=org.id,
                                              title=f"{org.name} paused: daily token budget "
                                                    f"({budget:,}) reached"))
                    await session.commit()
            await bus.publish("org.status", {"status": "paused", "reason": "daily token budget"},
                              org_id=org.id)
            return False
        if count >= per_hour:
            retry = (oldest or utcnow()) + timedelta(hours=1)
            await self._set_status(agent, "cooling_down",
                                   f"turn limit reached ({per_hour}/hour)", retry_at=retry)
            return False
        return True

    async def _claim(self, agent_id: str) -> list[Message]:
        async with SessionLocal() as session:
            rows = (await session.execute(text("""
                UPDATE deliveries SET status = 'claimed', attempts = attempts + 1,
                       lease_until = now() + make_interval(secs => :lease)
                WHERE id IN (
                    SELECT id FROM deliveries
                    WHERE agent_id = :agent AND status = 'pending' AND available_at <= now()
                    ORDER BY id LIMIT :lim FOR UPDATE SKIP LOCKED)
                RETURNING message_id
            """), {"agent": agent_id, "lease": settings.delivery_lease_seconds,
                   "lim": MAX_BATCH})).scalars().all()
            await session.commit()
            if not rows:
                return []
            msgs = (await session.execute(select(Message).where(Message.id.in_(rows))
                                          .order_by(Message.created_at))).scalars().all()
        return list(msgs)

    async def _mark_done(self, message_ids: list[str], agent_id: str, turn_id: str | None) -> None:
        async with SessionLocal() as session:
            await session.execute(update(Delivery).where(
                Delivery.agent_id == agent_id, Delivery.message_id.in_(message_ids),
            ).values(status="done", turn_id=turn_id, lease_until=None))
            await session.commit()

    async def _format_inbox(self, agent: Agent, msgs: list[Message]) -> tuple[str, int, list[str]]:
        async with SessionLocal() as session:
            people = {a.id: a for a in (await session.execute(
                select(Agent).where(Agent.org_id == agent.org_id))).scalars().all()}
            chan_ids = {m.channel_id for m in msgs if m.channel_id}
            chans = {c.id: c for c in (await session.execute(
                select(Channel).where(Channel.id.in_(chan_ids)))).scalars().all()} if chan_ids \
                else {}

        blocks, direct, images = [], [], []
        for m in msgs:

            def who(sid: str, m: Message = m) -> str:
                if sid == USER:
                    return "the user"
                a = people.get(sid)
                if a is None:
                    return str((m.meta or {}).get("senderName") or sid)
                return f"{a.name} ({a.role})" if a.role else a.name

            when = m.created_at.strftime("%H:%M") if m.created_at else ""
            ch = chans.get(m.channel_id) if m.channel_id else None
            if m.kind == "supervisor":
                head = f"Message from {who(m.sender_id)}"
            elif ch is not None and ch.kind == "dm":
                head = f"Direct message from {who(m.sender_id)}"
                if m.kind != "reply" and m.sender_type in ("user", "agent"):
                    direct.append(m.sender_id)
            elif ch is not None:
                head = f"{ch.key} — {who(m.sender_id)}"
            elif m.kind == "task_comment":
                head = f"Comment from {who(m.sender_id)} on {m.meta.get('taskRef', 'a task')}"
            elif m.kind == "meeting":
                head = "Meeting minutes"
            elif m.kind == "feature_request":
                head = "Feature request"
            elif m.kind == "agent_request":
                head = f"Request from {who(m.sender_id)}"
            elif m.kind == "activity":
                head = "Scheduled activity"
            elif m.kind == "speech":
                head = f"{who(m.sender_id)} says out loud in the {m.meta.get('room', 'room')}"
            elif m.kind == "observation":
                head = f"You notice (in the {m.meta.get('room', 'room')})"
            else:
                head = "Notification"
            sent = (m.meta or {}).get("attachments") or []
            if sent:
                listing = "; ".join(f"{a.get('name')} ({a.get('kind')}, id {a.get('id')})"
                                    for a in sent)
                text = (f"{m.content}\n[Attached: {listing}. Use attachment_read for documents "
                        "and attachment_view for images.]")
                images.extend(a["id"] for a in sent if a.get("kind") == "image")
            else:
                text = m.content
            blocks.append(f"[{head} · {when}]\n{text}")
        depth = max((m.depth for m in msgs), default=0)
        intro = "New in your inbox:\n\n" if len(blocks) > 1 else ""
        self._inbox_images = images
        return intro + "\n\n".join(blocks), depth, list(dict.fromkeys(direct))

    async def _inbox_image_message(self, agent: Agent, org: Org, turn_id: str
                                   ) -> list[HumanMessage]:
        """Images attached to the messages just formatted, shown to vision models."""
        ids, self._inbox_images = self._inbox_images[:4], []
        if not ids:
            return []
        try:
            from app.services import attachments

            if not (await resolve_for_agent(agent, org)).vision:
                return []
            blocks: list[dict[str, Any]] = [
                {"type": "text", "text": "[Images attached to the message above]"}]
            for att_id in ids:
                a = await attachments.get(att_id)
                if a is not None and a.status == "ready":
                    blocks.append({"type": "image_url", "image_url": {
                        "url": await asyncio.to_thread(attachments.image_data_url, a)}})
            if len(blocks) == 1:
                return []
            return [HumanMessage(content=blocks, additional_kwargs={"tool_image": True},
                                 id=f"inimg_{turn_id}")]
        except Exception:  # noqa: BLE001 - a bad image must never break the turn
            log.exception("couldn't attach images for %s", agent.name)
            return []

    # --- running the graph ----------------------------------------------------------

    async def _run(self, agent: Agent, org: Org, turn_id: str | None, *, resume: bool,
                   command: Command | None = None) -> None:
        cfg = thread_config(agent.id)
        await self._set_status(agent, "working", "")
        if turn_id and resume:
            async with SessionLocal() as session:
                await session.execute(update(Turn).where(Turn.id == turn_id).values(
                    status="running", attempts=Turn.attempts + (0 if command else 1)))
                await session.commit()
        try:
            await self.graph.ainvoke(command, cfg)
        except asyncio.CancelledError:
            if agent.id in self._stop_requested:
                self._stop_requested.discard(agent.id)
                await self._finish_stop(agent, org, turn_id)
            raise
        except Exception as e:  # noqa: BLE001
            await self._on_failure(agent, org, turn_id, e)
            return

        snapshot = await self.graph.aget_state(cfg)
        interrupts = _interrupts(snapshot)
        if snapshot.next and interrupts:
            await self._park_for_approval(agent, org, snapshot, interrupts)
            return
        await self._on_complete(agent, org, turn_id, snapshot)

    async def _on_complete(self, agent: Agent, org: Org, turn_id: str | None, snapshot) -> None:
        values = snapshot.values or {}
        messages = values.get("messages") or []
        final = messages[-1] if messages else None
        summary = text_of(final).strip() if isinstance(final, AIMessage) else ""
        try:
            if summary and turn_id:
                await self._auto_reply(agent, org, turn_id, summary,
                                       values.get("direct_senders") or [],
                                       int(values.get("depth") or 0))
        except Exception:  # noqa: BLE001 - never fail a finished turn on delivery issues
            log.exception("auto-reply failed for %s", agent.name)
        async with SessionLocal() as session:
            if turn_id:
                await session.execute(update(Turn).where(Turn.id == turn_id).values(
                    status="completed", summary=summary[:8000], ended_at=func.now(), error=None))
            await session.execute(update(Agent).where(Agent.id == agent.id).values(
                consecutive_failures=0, retry_at=None))
            await session.commit()
        await bus.publish("turn.completed", {"turnId": turn_id, "summary": summary[:2000]},
                          org_id=org.id, agent_id=agent.id)
        await self._set_status(agent, "idle", "")

    async def _auto_reply(self, agent: Agent, org: Org, turn_id: str, text_: str,
                          direct: list[str], depth: int) -> None:
        """Deliver the final reply to direct-message senders the agent didn't answer."""
        if not direct:
            return
        async with SessionLocal() as session:
            sent_channels = set((await session.execute(select(Message.channel_id).where(
                Message.turn_id == turn_id, Message.sender_id == agent.id))).scalars().all())
        for sender in direct:
            key = comms.dm_key(agent.id, sender)
            async with SessionLocal() as session:
                ch = (await session.execute(select(Channel).where(
                    Channel.org_id == org.id, Channel.key == key))).scalar_one_or_none()
            if ch is None or ch.id in sent_channels:
                continue
            await comms.post(org.id, Sender("agent", agent.id, turn_id=turn_id, depth=depth),
                             text_, channel=ch, kind="reply")

    async def _on_failure(self, agent: Agent, org: Org, turn_id: str | None, exc: Exception) -> None:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        log.warning("turn failed for %s: %s", agent.name, error)
        async with SessionLocal() as session:
            a = await session.get(Agent, agent.id)
            if a is None:
                return
            a.consecutive_failures = (a.consecutive_failures or 0) + 1
            failures = a.consecutive_failures
            if turn_id:
                await session.execute(update(Turn).where(Turn.id == turn_id).values(
                    status="failed", error=error, ended_at=func.now()))
            parked = failures >= settings.max_turn_failures
            if parked:
                a.runtime_status, a.runtime_detail, a.retry_at = "error", error, None
                session.add(UserInboxItem(org_id=org.id, kind="error", ref_id=agent.id,
                                          title=f"{agent.name} stopped after {failures} failed "
                                                f"attempts: {error[:300]}"))
            else:
                delay = BACKOFF[min(failures - 1, len(BACKOFF) - 1)]
                a.runtime_status = "retrying"
                a.runtime_detail = f"attempt {failures} failed: {error}"
                a.retry_at = utcnow() + timedelta(seconds=delay)
            await session.commit()
            status, detail = a.runtime_status, a.runtime_detail
        await bus.publish("turn.failed", {"turnId": turn_id, "error": error,
                                          "failures": failures, "parked": parked},
                          org_id=org.id, agent_id=agent.id)
        await bus.publish("agent.status", {"status": status, "detail": detail},
                          org_id=org.id, agent_id=agent.id)

    # --- approvals ------------------------------------------------------------------

    async def _park_for_approval(self, agent: Agent, org: Org, snapshot, interrupts) -> None:
        turn_id = (snapshot.values or {}).get("turn_id")
        created = []
        async with SessionLocal() as session:
            for intr in interrupts:
                for req in (intr.value or {}).get("requests", []):
                    existing = (await session.execute(select(Approval).where(
                        Approval.tool_call_id == req["toolCallId"]))).scalar_one_or_none()
                    if existing is None:
                        row = Approval(org_id=org.id, agent_id=agent.id, turn_id=turn_id,
                                       tool_call_id=req["toolCallId"], tool=req["tool"],
                                       args=req.get("args") or {})
                        session.add(row)
                        await session.flush()
                        session.add(UserInboxItem(org_id=org.id, kind="approval", ref_id=row.id,
                                                  title=f"{agent.name} wants to run {req['tool']}"))
                        created.append(row)
            if turn_id:
                await session.execute(update(Turn).where(Turn.id == turn_id)
                                      .values(status="awaiting_approval"))
            detail = ", ".join(sorted({r.tool for r in created})) or "tool approval"
            # Same transaction: nobody can observe a pending approval on a "working" agent.
            await session.execute(update(Agent).where(Agent.id == agent.id).values(
                runtime_status="awaiting_approval", runtime_detail=detail))
            await session.commit()
        agent.runtime_status = "awaiting_approval"
        await bus.publish("agent.status", {"status": "awaiting_approval", "detail": detail},
                          org_id=org.id, agent_id=agent.id)
        for row in created:
            await bus.publish("approval.requested", approval_to_dict(row), org_id=org.id,
                              agent_id=agent.id)

    async def decide(self, approval_id: str, approved: bool, note: str = "") -> Approval:
        async with SessionLocal() as session:
            row = await session.get(Approval, approval_id)
            if row is None:
                raise KeyError(approval_id)
            if row.status != "pending":
                return row
            row.status = "approved" if approved else "denied"
            row.note = note
            row.decided_at = utcnow()
            await session.commit()
            pending = (await session.execute(select(Approval).where(
                Approval.agent_id == row.agent_id, Approval.status == "pending"))).scalars().all()
            decided = (await session.execute(select(Approval).where(
                Approval.agent_id == row.agent_id, Approval.turn_id == row.turn_id,
                Approval.status != "pending"))).scalars().all()
        await bus.publish("approval.decided", approval_to_dict(row), org_id=row.org_id,
                          agent_id=row.agent_id)
        if not pending:
            values = {a.tool_call_id: {"approved": a.status == "approved", "note": a.note}
                      for a in decided}
            self._launch_resume(row.agent_id, values)
        return row

    def _launch_resume(self, agent_id: str, values: dict[str, Any]) -> None:
        async def go() -> None:
            async with self._sem:
                async with SessionLocal() as session:
                    agent = await session.get(Agent, agent_id)
                    org = await session.get(Org, agent.org_id) if agent else None
                if agent is None or org is None:
                    return
                snapshot = await self.graph.aget_state(thread_config(agent_id))
                turn_id = (snapshot.values or {}).get("turn_id")
                async with SessionLocal() as session:
                    await session.execute(update(Agent).where(Agent.id == agent_id).values(
                        runtime_status="working"))
                    await session.commit()
                await self._run(agent, org, turn_id, resume=True, command=Command(resume=values))

        async def wrapper() -> None:
            prior = self._workers.get(agent_id)
            if prior is not None:
                await asyncio.gather(prior, return_exceptions=True)
            task = asyncio.current_task()
            assert task is not None
            self._workers[agent_id] = task
            try:
                await go()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("resume after approval failed for %s", agent_id)
            finally:
                if self._workers.get(agent_id) is task:
                    self._workers.pop(agent_id, None)
                self.wake(agent_id)

        asyncio.create_task(wrapper(), name=f"resume:{agent_id}")

    # --- user controls --------------------------------------------------------------

    async def stop_turn(self, agent_id: str) -> bool:
        """Cancel the agent's current turn and close it cleanly."""
        worker = self._workers.get(agent_id)
        if worker is not None and not worker.done():
            self._stop_requested.add(agent_id)
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            return True
        async with SessionLocal() as session:
            agent = await session.get(Agent, agent_id)
            org = await session.get(Org, agent.org_id) if agent else None
        if agent is None or org is None:
            return False
        snapshot = await self.graph.aget_state(thread_config(agent_id))
        if snapshot.next:
            await self._finish_stop(agent, org, (snapshot.values or {}).get("turn_id"))
            return True
        return False

    async def _finish_stop(self, agent: Agent, org: Org, turn_id: str | None) -> None:
        cfg = thread_config(agent.id)
        snapshot = await self.graph.aget_state(cfg)
        if snapshot.next:
            messages = (snapshot.values or {}).get("messages") or []
            answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
            patch: list[Any] = []
            last_ai = next((m for m in reversed(messages) if isinstance(m, AIMessage)), None)
            if last_ai is not None:
                for tc in last_ai.tool_calls:
                    if tc.get("id") and tc["id"] not in answered:
                        patch.append(ToolMessage(content="(cancelled: the user stopped this turn)",
                                                 tool_call_id=tc["id"], name=tc["name"],
                                                 status="error"))
            patch.append(AIMessage(content="(This turn was stopped by the user.)"))
            await self.graph.aupdate_state(cfg, {"messages": patch}, as_node="model")
        async with SessionLocal() as session:
            await session.execute(update(Approval).where(
                Approval.agent_id == agent.id, Approval.status == "pending").values(
                status="denied", note="turn stopped", decided_at=func.now()))
            if turn_id:
                await session.execute(update(Turn).where(Turn.id == turn_id).values(
                    status="stopped", ended_at=func.now()))
            await session.commit()
        await bus.publish("turn.stopped", {"turnId": turn_id}, org_id=org.id, agent_id=agent.id)
        await self._set_status(agent, "idle", "")

    async def retry(self, agent_id: str) -> None:
        async with SessionLocal() as session:
            await session.execute(update(Agent).where(Agent.id == agent_id).values(
                runtime_status="retrying", runtime_detail="manual retry", retry_at=None,
                consecutive_failures=0))
            await session.commit()
        self.wake(agent_id)

    async def _exclusive(self, agent_id: str, fn: Callable[[], Awaitable[Any]]) -> Any:
        """Run ``fn`` as the agent's worker, so no turn can start while it runs."""

        async def wrapper() -> Any:
            prior = self._workers.get(agent_id)
            if prior is not None:
                await asyncio.gather(prior, return_exceptions=True)
            task = asyncio.current_task()
            assert task is not None
            self._workers[agent_id] = task
            try:
                return await fn()
            finally:
                if self._workers.get(agent_id) is task:
                    self._workers.pop(agent_id, None)
                self.wake(agent_id)

        return await asyncio.create_task(wrapper(), name=f"control:{agent_id}")

    async def _mark_in_dm(self, agent: Agent, text: str, kind: str) -> None:
        """Leave a visible marker in the user's DM with the agent (not delivered)."""
        async with SessionLocal() as session:
            channel = (await session.execute(select(Channel).where(
                Channel.org_id == agent.org_id,
                Channel.key == comms.dm_key(agent.id, comms.USER)))).scalar_one_or_none()
        if channel is not None:
            await comms.post(agent.org_id, comms.Sender.system(), text, channel=channel,
                             kind=kind, deliver=False)

    async def reset_memory(self, agent_id: str) -> None:
        """Clear the agent's context: its working thread and summary.

        Long-term memories (``remember``), tasks, messages and history stay; the
        agent simply starts its next turn with a fresh context.
        """
        await self.stop_turn(agent_id)

        async def clear() -> None:
            await self.saver.adelete_thread(f"agent:{agent_id}")
            async with SessionLocal() as session:
                await session.execute(update(Approval).where(
                    Approval.agent_id == agent_id, Approval.status == "pending").values(
                    status="denied", note="context cleared", decided_at=func.now()))
                await session.execute(update(Agent).where(Agent.id == agent_id).values(
                    runtime_status="idle", runtime_detail="", consecutive_failures=0,
                    retry_at=None))
                await session.commit()
                agent = await session.get(Agent, agent_id)
            if agent is None:
                return
            await bus.publish("agent.status", {"status": "idle", "detail": ""},
                              org_id=agent.org_id, agent_id=agent_id)
            await bus.publish("agent.context_cleared", {}, org_id=agent.org_id,
                              agent_id=agent_id)
            await self._mark_in_dm(agent, f"{agent.name}'s context was cleared. Earlier "
                                   "messages stay here for you, but the agent no longer "
                                   "sees them.", "context_cleared")

        await self._exclusive(agent_id, clear)

    async def compact_now(self, agent_id: str) -> dict[str, Any]:
        """Summarize the agent's whole working thread now (the agent must be idle)."""
        from app.agents.graph import compaction_split, summarize  # noqa: PLC0415

        async def go() -> dict[str, Any]:
            cfg = thread_config(agent_id)
            snapshot = await self.graph.aget_state(cfg)
            if snapshot.next:
                raise RuntimeBusy("the agent is in the middle of a turn; try when it's idle")
            values = snapshot.values or {}
            messages = list(values.get("messages") or [])
            async with SessionLocal() as session:
                agent = await session.get(Agent, agent_id)
                org = await session.get(Org, agent.org_id) if agent else None
            if agent is None or org is None:
                raise RuntimeBusy("agent not found")
            last = messages[-1] if messages else None
            if not isinstance(last, AIMessage) or last.tool_calls:
                return {"removed": 0, "summary": values.get("summary", "")}
            old = messages[:compaction_split(messages, 0, force=True)]
            if not old:
                return {"removed": 0, "summary": values.get("summary", "")}
            model = await resolve_for_agent(agent, org)
            summary = await summarize(org, agent, model, values.get("summary", ""), old, None)
            # As the model node: routing sees the final reply and ends, so nothing runs.
            await self.graph.aupdate_state(
                cfg, {"summary": summary,
                      "messages": [RemoveMessage(id=m.id) for m in old if m.id]},
                as_node="model")
            await bus.publish("agent.compacted", {"removed": len(old), "manual": True},
                              org_id=org.id, agent_id=agent_id)
            await self._mark_in_dm(agent, f"{agent.name}'s context was compacted into a "
                                   "summary.", "context_compacted")
            return {"removed": len(old), "summary": summary}

        if self.is_busy(agent_id):
            raise RuntimeBusy("the agent is working; try when it's idle")
        return await self._exclusive(agent_id, go)

    def is_busy(self, agent_id: str) -> bool:
        w = self._workers.get(agent_id)
        return w is not None and not w.done()

    async def thread_state(self, agent_id: str) -> dict[str, Any]:
        snapshot = await self.graph.aget_state(thread_config(agent_id))
        values = snapshot.values or {}
        from app.agents.llm import serialize_messages

        messages = values.get("messages") or []
        context: dict[str, Any] = {"tokens": count_tokens_approximately(messages)}
        async with SessionLocal() as session:
            agent = await session.get(Agent, agent_id)
            org = await session.get(Org, agent.org_id) if agent else None
        if agent is not None and org is not None:
            try:
                window = (await resolve_for_agent(agent, org)).context_window
                context |= {"window": window, "compactAt": int(window * COMPACT_AT)}
            except Exception:  # noqa: BLE001 - usage is informational
                pass
        return {
            "next": list(snapshot.next),
            "context": context,
            "summary": values.get("summary", ""),
            "turnId": values.get("turn_id"),
            "steps": values.get("steps", 0),
            "messages": serialize_messages(values.get("messages") or []),
        }

    async def _set_status(self, agent: Agent, status: str, detail: str,
                          retry_at: Any = None) -> None:
        async with SessionLocal() as session:
            values: dict[str, Any] = {"runtime_status": status, "runtime_detail": detail[:2000]}
            if retry_at is not None:
                values["retry_at"] = retry_at
            await session.execute(update(Agent).where(Agent.id == agent.id).values(**values))
            await session.commit()
        agent.runtime_status = status
        await bus.publish("agent.status", {"status": status, "detail": detail[:500]},
                          org_id=agent.org_id, agent_id=agent.id)


def _interrupts(snapshot) -> list:
    found = list(getattr(snapshot, "interrupts", ()) or ())
    if not found:
        for task in getattr(snapshot, "tasks", ()) or ():
            found.extend(getattr(task, "interrupts", ()) or ())
    return found


def approval_to_dict(a: Approval) -> dict[str, Any]:
    return {"id": a.id, "orgId": a.org_id, "agentId": a.agent_id, "turnId": a.turn_id,
            "toolCallId": a.tool_call_id, "tool": a.tool, "args": a.args, "status": a.status,
            "note": a.note, "createdAt": a.created_at.isoformat() if a.created_at else None,
            "decidedAt": a.decided_at.isoformat() if a.decided_at else None}


runtime = Runtime()
