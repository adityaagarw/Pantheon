"""Turns for agents of other kinds (app/agents/kinds.py): CLI agents, remote agents.

The runtime claims the agent's messages as usual and hands the turn here. The agent's kind
runs it (a Claude Code process, an ACP session, an HTTP call…) with the inbox as text, the
agent's context and a bridge to Pantheon's tools, and its reply is delivered like any
agent's: to whoever wrote to it, and as the result of a flow step it was doing.

While it runs, ``Live`` makes it a first-class member of the organization:
- its own tools (a shell command, a file edit) show in the office, Sessions and Activity
  like Pantheon's, and its thinking streams as a thought cloud;
- its tokens and cost count on the turn, so budgets and stats include it;
- what it asks permission for waits in the user's Inbox as an approval, like any agent's;
- the user's steering reaches it mid-turn;
- a provider's usage limit parks its work until the limit resets (QuotaWait), instead of
  failing turn after turn.

The bridge is a stdio MCP server (``python -m app.mcpbridge``) the agent's process starts.
It relays to the engine with a token that's good for this turn only, and offers the
agent's Pantheon tools except the ones it has its own of (files, shell); the ones that need
the user's approval ask for it (api/bridge.py).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import sys
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update

from app.agents import kinds
from app.agents.prompt import build_system_prompt
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.ids import new_id, utcnow
from app.events import bus
from app.models import Agent, Approval, Delivery, Org, Turn, UserInboxItem
from app.services import accounts
from app.tools.base import agent_workdir

log = logging.getLogger(__name__)

# What CLI agents do themselves, in their own folder.
OWN_TOOLS = {"read_file", "write_file", "edit_file", "list_dir", "search_files", "run_command",
             "computer_shell", "spawn_subagent"}

EXTERNAL_RULES = """\
# Working in Pantheon
You're a member of a Pantheon organization. Pantheon's tools are yours through the MCP
server "pantheon": messaging colleagues (send_message), tasks, the board, memory, flow
steps (finish_step) and more. Your own tools work as usual, in your working folder.
Your final reply goes back to whoever wrote to you, so make it the answer itself."""
# Zeus, Argus and Kronos run the platform rather than work in an organization.
META_EXTERNAL_RULES = """\
# Working in Pantheon
You're one of Pantheon's built-in agents. The tools your instructions name (organizations,
agents, flows, oversight, Pantheon's code…) are yours through the MCP server "pantheon":
use those, not files or commands, to change Pantheon. Your own tools work as usual, in your
working folder. Your final reply goes back to whoever wrote to you, so make it the answer
itself."""

RECENT_TURNS = 6


@dataclass
class Grant:
    agent_id: str
    turn_id: str
    depth: int
    tools: set[str]
    live: Live | None = None


_grants: dict[str, Grant] = {}  # bridge token -> what it may do
# Approvals an external turn is waiting on: approval id -> its answer (approved, note).
_waiting: dict[str, asyncio.Future[tuple[bool, str]]] = {}


def grant(token: str) -> Grant | None:
    return _grants.get(token)


def resolve_approval(approval_id: str, approved: bool, note: str) -> bool:
    """The user decided an approval an external turn waits on (runtime.decide)."""
    fut = _waiting.get(approval_id)
    if fut is None:
        return False
    if not fut.done():
        fut.set_result((approved, note))
    return True


async def bridge_tools(org: Org, agent: Agent) -> dict[str, Any]:
    """The agent's Pantheon tools an external agent gets through the bridge (the ones that
    need approval ask for it there)."""
    from app.tools.resolve import resolve_tools

    resolved = (await resolve_tools(org, agent)).tools
    return {n: t for n, t in resolved.items() if n not in OWN_TOOLS and t.approval != "deny"}


@dataclass
class Live(kinds.Live):
    """The running turn, as the interface and the user see it."""

    runtime: Any
    agent: Agent
    org: Org
    turn_id: str
    direct: list[str]
    calls: dict[str, tuple[str, str, dict[str, Any], float]] = field(default_factory=dict)
    account_id: str = ""  # the account it works with (services/accounts.py), if any

    async def _publish(self, event: str, payload: dict[str, Any], persist: bool = True) -> None:
        await bus.publish(event, {**payload, "turnId": self.turn_id}, org_id=self.org.id,
                          agent_id=self.agent.id, persist=persist)

    async def think(self, text: str) -> None:
        if text:
            await self._publish("agent.thinking", {"delta": text}, persist=False)

    async def status(self, detail: str) -> None:
        await self._publish("agent.status", {"status": "working", "detail": detail[:500]},
                            persist=False)

    async def tool_started(self, call_id: str, name: str, args: dict[str, Any]) -> None:
        if call_id in self.calls:
            return
        tc_id = new_id("tc")
        self.calls[call_id] = (tc_id, name, args, time.monotonic())
        await self._publish("tool.started", {"toolCallId": tc_id, "tool": name, "args": args,
                                             "source": self.agent.kind})
        await self.status(f"using {name}")

    async def tool_finished(self, call_id: str, ok: bool, output: str) -> None:
        from app.agents.graph import _record_tool_call

        found = self.calls.pop(call_id, None)
        if found is None:
            return
        tc_id, name, args, started = found
        duration = int((time.monotonic() - started) * 1000)
        status = "ok" if ok else "error"
        await _record_tool_call(tc_id, self.org.id, self.agent.id, self.turn_id, name, args,
                                status, output, duration)
        async with SessionLocal() as session:
            await session.execute(update(Turn).where(Turn.id == self.turn_id).values(
                steps=Turn.steps + 1))
            await session.commit()
        await self._publish("tool.completed", {"toolCallId": tc_id, "tool": name,
                                               "status": status, "durationMs": duration,
                                               "preview": output[:600]})

    async def usage(self, input_tokens: int = 0, output_tokens: int = 0,
                    cost_usd: float = 0.0) -> None:
        if not (input_tokens or output_tokens or cost_usd):
            return
        async with SessionLocal() as session:
            await session.execute(update(Turn).where(Turn.id == self.turn_id).values(
                input_tokens=Turn.input_tokens + int(input_tokens),
                output_tokens=Turn.output_tokens + int(output_tokens),
                cost_usd=Turn.cost_usd + float(cost_usd)))
            await session.commit()
        if self.account_id:
            await accounts.add_usage(self.account_id, input_tokens, output_tokens, cost_usd)
        await self._publish("turn.usage", {"inputTokens": input_tokens,
                                           "outputTokens": output_tokens, "costUsd": cost_usd},
                            persist=False)

    async def ask(self, action: str, args: dict[str, Any], why: str = "") -> tuple[bool, str]:
        """An approval in the user's Inbox, like a Pantheon agent's; the turn waits for it."""
        from app.agents.runtime import approval_to_dict

        title = f"{self.agent.name} wants to {why}" if why else \
            f"{self.agent.name} wants to run {action}"
        async with SessionLocal() as session:
            row = Approval(org_id=self.org.id, agent_id=self.agent.id, turn_id=self.turn_id,
                           tool_call_id=new_id("tc"), tool=action[:200],
                           args=args if isinstance(args, dict) else {"input": args})
            session.add(row)
            await session.flush()
            session.add(UserInboxItem(org_id=self.org.id, kind="approval", ref_id=row.id,
                                      title=title[:300]))
            await session.execute(update(Turn).where(Turn.id == self.turn_id)
                                  .values(status="awaiting_approval"))
            await session.execute(update(Agent).where(Agent.id == self.agent.id).values(
                runtime_status="awaiting_approval", runtime_detail=action[:500]))
            await session.commit()
        fut: asyncio.Future[tuple[bool, str]] = asyncio.get_running_loop().create_future()
        _waiting[row.id] = fut
        await bus.publish("agent.status", {"status": "awaiting_approval", "detail": action[:500]},
                          org_id=self.org.id, agent_id=self.agent.id)
        await bus.publish("approval.requested", approval_to_dict(row), org_id=self.org.id,
                          agent_id=self.agent.id)
        try:
            approved, note = await fut
        except asyncio.CancelledError:
            async with SessionLocal() as session:
                await session.execute(update(Approval).where(
                    Approval.id == row.id, Approval.status == "pending").values(
                    status="denied", note="turn stopped", decided_at=func.now()))
                await session.commit()
            raise
        finally:
            _waiting.pop(row.id, None)
        async with SessionLocal() as session:
            await session.execute(update(Turn).where(Turn.id == self.turn_id)
                                  .values(status="running"))
            await session.commit()
        await self.runtime._set_status(self.agent, "working",
                                       f"{'approved' if approved else 'denied'}: {action}")
        return approved, note

    async def steering(self) -> list[str]:
        from app.agents import steering

        try:
            msgs = await steering.take(self.agent.id, self.turn_id)
        except Exception:  # noqa: BLE001 - a missed steer waits for the next turn instead
            log.exception("taking %s's steering failed", self.agent.name)
            return []
        if not msgs:
            return []
        content, _depth, direct = await self.runtime._format_inbox(self.agent, msgs)
        self.direct.extend(d for d in direct if d not in self.direct)
        await self._publish("agent.steered", {"messageIds": [m.id for m in msgs]})
        return [f"{steering.STEER_NOTE}\n\n{content}"]


def _bridge(agent: Agent, turn_id: str, token: str, tools: list[str]) -> kinds.Bridge:
    return kinds.Bridge(
        command=sys.executable, args=["-m", "app.mcpbridge"],
        env={"PANTHEON_BRIDGE_URL": f"http://127.0.0.1:{settings.port}",
             "PANTHEON_BRIDGE_TOKEN": token, "PANTHEON_BRIDGE_AGENT": agent.id,
             "PYTHONPATH": str(_engine_root())},
        tools=tools)


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


async def recent_work(agent_id: str, before: str) -> str:
    """The agent's last few turns, briefly: what a fresh session needs to carry on."""
    async with SessionLocal() as session:
        rows = list((await session.execute(select(Turn).where(
            Turn.agent_id == agent_id, Turn.id != before, Turn.status == "completed",
            Turn.summary != "").order_by(Turn.started_at.desc()).limit(RECENT_TURNS))).scalars())
    lines = []
    for t in reversed(rows):
        when = t.started_at.strftime("%Y-%m-%d %H:%M") if t.started_at else ""
        text = (t.summary or "").strip().replace("\n", " ")
        lines.append(f"- {when}: {text[:600]}{'…' if len(text) > 600 else ''}")
    return "\n".join(lines)


async def run(runtime: Any, agent: Agent, org: Org, turn_id: str, inbox: str,
              direct: list[str], depth: int) -> None:
    """One turn of an external agent, from start to delivered reply."""
    kind = kinds.get(agent.kind)
    if kind is None or kind.run is None:
        await runtime._on_failure(agent, org, turn_id, RuntimeError(
            f"no agent kind '{agent.kind}' (is its pack switched off?)"))
        return
    await runtime._set_status(agent, "working", f"{kind.label} is working")
    tools = await bridge_tools(org, agent)
    token = secrets.token_urlsafe(24)
    direct = list(direct)
    live = Live(runtime=runtime, agent=agent, org=org, turn_id=turn_id, direct=direct)
    _grants[token] = Grant(agent.id, turn_id, depth, set(tools), live)

    async def emit(text: str) -> None:
        if text:
            await bus.publish("agent.stream", {"turnId": turn_id, "delta": text},
                              org_id=org.id, agent_id=agent.id, persist=False)

    try:
        account = await accounts.choose(agent.runner or {}) if agent.kind == "cli" else \
            accounts.Choice()
        live.account_id = account.account.id if account.account else ""
        system = await build_system_prompt(org, agent, tool_names=list(tools)) + "\n\n" + \
            (META_EXTERNAL_RULES if agent.is_supervisor else EXTERNAL_RULES)
        turn = kinds.ExternalTurn(
            agent=agent, org=org, turn_id=turn_id, inbox=inbox, system=system,
            workdir=agent_workdir(org, agent), bridge=_bridge(agent, turn_id, token, list(tools)),
            emit=emit, runner=dict(agent.runner or {}), live=live,
            recent=await recent_work(agent.id, turn_id), env=account.env)
        reply = (await kind.run(turn) or "").strip()
    except asyncio.CancelledError:
        await _stopped(runtime, agent, org, turn_id)
        raise
    except kinds.QuotaWait as q:
        if live.account_id:  # every agent on that account waits with it
            await accounts.cool(live.account_id, q.until if q.until and q.until > utcnow()
                                else utcnow() + timedelta(hours=1), str(q))
        await _requeue(agent.id, turn_id)
        await _wait_for_quota(runtime, agent, org, turn_id, q)
        return
    except Exception as e:  # noqa: BLE001 - the runtime records and retries like any failure
        # A tool's error can repeat its command line, bridge token included: never keep it.
        e = RuntimeError(str(e).replace(token, "[bridge token]")) if token in str(e) else e
        log.warning("%s (%s) failed: %s", agent.name, agent.kind, e)
        await _requeue(agent.id, turn_id)
        await runtime._on_failure(agent, org, turn_id, e)
        return
    finally:
        _grants.pop(token, None)
    await complete(runtime, agent, org, turn_id, reply, direct, depth)


async def _requeue(agent_id: str, turn_id: str) -> None:
    """A failed turn's messages go back in the inbox: the retry (after the runtime's
    backoff) takes them again, as an LLM agent resumes from its checkpoint."""
    async with SessionLocal() as session:
        await session.execute(update(Delivery).where(
            Delivery.agent_id == agent_id, Delivery.turn_id == turn_id)
            .values(status="pending", turn_id=None, lease_until=None))
        await session.commit()


async def _wait_for_quota(runtime: Any, agent: Agent, org: Org, turn_id: str,
                          q: kinds.QuotaWait) -> None:
    """Out of quota isn't a failure: the work waits for the limit to reset, then goes on."""
    until = q.until if q.until and q.until > utcnow() else utcnow() + timedelta(hours=1)
    detail = f"usage limit reached; picks up again at {until.astimezone():%H:%M} ({q})"
    async with SessionLocal() as session:
        await session.execute(update(Turn).where(Turn.id == turn_id).values(
            status="failed", error=f"waiting for the usage limit: {q}"[:4000],
            ended_at=func.now()))
        await session.commit()
    await bus.publish("turn.failed", {"turnId": turn_id, "error": detail, "failures": 0,
                                      "parked": False, "quota": True},
                      org_id=org.id, agent_id=agent.id)
    await runtime._set_status(agent, "cooling_down", detail, retry_at=until)


async def complete(runtime: Any, agent: Agent, org: Org, turn_id: str, reply: str,
                   direct: list[str], depth: int) -> None:
    """Deliver the reply and close the turn, as for an LLM agent's (runtime._on_complete)."""
    from app.services import flows

    try:
        if reply:
            await runtime._auto_reply(agent, org, turn_id, reply, direct, depth)
    except Exception:  # noqa: BLE001
        log.exception("delivering %s's reply failed", agent.name)
    try:
        await flows.on_turn_complete(agent.id, org.id, turn_id, reply)
    except Exception:  # noqa: BLE001
        log.exception("flow step completion failed for %s", agent.name)
    async with SessionLocal() as session:
        await session.execute(update(Turn).where(Turn.id == turn_id).values(
            status="completed", summary=reply[:8000], ended_at=func.now(), error=None))
        await session.execute(update(Agent).where(Agent.id == agent.id).values(
            consecutive_failures=0, retry_at=None))
        await session.commit()
    await bus.publish("turn.completed", {"turnId": turn_id, "summary": reply[:2000]},
                      org_id=org.id, agent_id=agent.id)
    await runtime._set_status(agent, "idle", "")


async def _stopped(runtime: Any, agent: Agent, org: Org, turn_id: str) -> None:
    runtime._stop_requested.discard(agent.id)
    async with SessionLocal() as session:
        await session.execute(update(Turn).where(Turn.id == turn_id).values(
            status="stopped", ended_at=func.now()))
        await session.commit()
    await bus.publish("turn.stopped", {"turnId": turn_id}, org_id=org.id, agent_id=agent.id)
    await runtime._set_status(agent, "idle", "")
