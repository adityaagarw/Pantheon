"""The agent graph (LangGraph).

Each agent has ONE durable thread (``agent:<id>``) — its continuous working
memory. A turn is:

    inbox ──► prepare ──► model ──► gate ──► tool (one task per call, parallel)
                            ▲                   │
                            └───────────────────┘
                            └──► END (reply with no tool calls)

Durability guarantees:
- The runtime appends inbox messages with ``aupdate_state(as_node="inbox")``
  (one atomic checkpoint), then runs ``ainvoke(None)``. A crash at any point
  resumes from the last checkpoint.
- Each tool call is its own task (``Send``) so completed calls' results are
  saved as pending writes; on resume only unfinished calls run again.
- Tool results are additionally keyed by ``tool_call_id`` in ``tool_calls``;
  a call that finished but whose write was lost is not executed twice.
- Approvals use ``interrupt()``; the gate node has no side effects, so its
  re-execution on resume is safe.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from sqlalchemy import update

from app.agents import llm, nudges
from app.agents.prompt import build_system_prompt
from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.llm.providers import resolve_for_agent
from app.models import Agent, Org, ToolCall, Turn
from app.tools import loadout
from app.tools.base import ToolContext
from app.tools.resolve import clip, execute, resolve_tools

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 600
COMPACT_AT = 0.6  # fraction of the context window (unless the model names a compact_at)
KEEP_RECENT = 0.4  # fraction of the compaction point kept verbatim after compaction
CHARS_PER_TOKEN = 3.2  # what current tokenizers average on English, code and JSON
PROMPT_OVERHEAD = 8000  # system prompt and tool schemas, when nothing was measured yet


# What a picture or a video costs a model, roughly: its data: URL's length says nothing (a
# 2 MB video is ~2.7M characters of base64). Radiance encodes an image to 280 tokens by
# default and a video to at most its frame budget.
IMAGE_TOKENS = 1000
VIDEO_TOKENS = 16000


def _content_chars(content: Any) -> int:
    if not isinstance(content, list):
        return len(str(content))
    chars = 0
    for part in content:
        kind = part.get("type") if isinstance(part, dict) else None
        if kind in ("image_url", "image"):
            chars += int(IMAGE_TOKENS * CHARS_PER_TOKEN)
        elif kind == "video_url":
            chars += int(VIDEO_TOKENS * CHARS_PER_TOKEN)
        else:
            chars += len(str(part))
    return chars


def estimate_tokens(messages: list[BaseMessage]) -> int:
    """Tokens a transcript costs, erring high: chars/4 undercounts by a quarter or more."""
    chars = sum(_content_chars(m.content) + sum(len(str(tc.get("args", ""))) + 40
                                                for tc in getattr(m, "tool_calls", None) or [])
                for m in messages)
    return int(chars / CHARS_PER_TOKEN) + 4 * len(messages)


def compact_point(model: Any) -> int:
    return int(model.compact_at or model.context_window * COMPACT_AT)


def context_size(state: AgentState) -> int:
    """The prompt the next model call will send, in tokens: what the last call measured
    (system prompt and tool schemas included) plus what arrived since; an estimate with
    room for the system prompt when nothing has been measured."""
    messages = state.get("messages", [])
    measured = int(state.get("context_tokens") or 0)
    if not measured:
        return estimate_tokens(messages) + PROMPT_OVERHEAD
    last_ai = max((i for i, m in enumerate(messages) if isinstance(m, AIMessage)), default=-1)
    return measured + estimate_tokens(messages[last_ai + 1:])


def _merge_seen(old: list[str] | None, new: list[str] | None) -> list[str]:
    old = list(old or [])
    have = set(old)
    old.extend(x for x in new or [] if x not in have)
    return old[-2000:]


def _merge_decisions(old: dict | None, new: dict | None) -> dict:
    if new is None:
        return dict(old or {})
    if new.get("__reset__"):
        return {}
    return {**(old or {}), **new}


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    summary: str
    seen: Annotated[list[str], _merge_seen]
    decisions: Annotated[dict, _merge_decisions]
    turn_id: str
    depth: int
    steps: int
    direct_senders: list[str]
    recalled: str  # memories auto-recalled for the current turn
    recalled_files: str  # passages from the agent's files, likewise
    context_tokens: int  # input + output tokens of the last model call (0: not measured)
    system_prompt: str  # this turn's system prompt, reused on every step (prompt caching)
    system_key: str  # the turn (and summary) it was built for
    noted: dict  # when this turn's notes were last given (nudges.py)
    sticky: list[str]  # tools used lately, kept offered this turn (loadout.recent_tools)


# A step is four graph hops (prepare, model, gate, tool); max_steps_per_turn is what ends a
# turn, so LangGraph's own limit must never come first.
RECURSION_LIMIT = 10_000


def thread_config(agent_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": f"agent:{agent_id}", "agent_id": agent_id},
            "recursion_limit": RECURSION_LIMIT}


async def _load(config: RunnableConfig) -> tuple[Org, Agent]:
    agent_id = config["configurable"]["agent_id"]
    async with SessionLocal() as session:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            raise RuntimeError(f"agent {agent_id} no longer exists")
        org = await session.get(Org, agent.org_id)
        assert org is not None
    return org, agent


# --- nodes --------------------------------------------------------------------------


async def inbox_node(state: AgentState) -> dict:
    return {}


def sanitize(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Guarantee a provider-valid transcript: every tool call answered, no orphans."""
    out: list[BaseMessage] = []
    pending: dict[str, str] = {}
    deferred: list[BaseMessage] = []  # screenshots wait until every tool result is in
    for m in messages:
        if isinstance(m, ToolMessage):
            if m.tool_call_id in pending:
                pending.pop(m.tool_call_id)
                out.append(m)
                if not pending and deferred:
                    out.extend(deferred)
                    deferred = []
            continue
        if pending and (m.additional_kwargs or {}).get("tool_image"):
            deferred.append(m)
            continue
        if pending:
            for tc_id, name in pending.items():
                out.append(ToolMessage(content="(no result: the call was interrupted)",
                                       tool_call_id=tc_id, name=name, status="error"))
            pending = {}
            out.extend(deferred)
            deferred = []
        out.append(m)
        if isinstance(m, AIMessage):
            for tc in m.tool_calls:
                if tc.get("id"):
                    pending[tc["id"]] = tc["name"]
    for tc_id, name in pending.items():
        out.append(ToolMessage(content="(no result: the call was interrupted)",
                               tool_call_id=tc_id, name=name, status="error"))
    out.extend(deferred)
    while out and not isinstance(out[0], HumanMessage):
        out.pop(0)  # providers require the conversation to start with a user turn
    return out


def compaction_split(messages: list[BaseMessage], window: int, *, force: bool = False) -> int:
    """Index where the verbatim tail starts; everything before it gets summarized.

    Keeps roughly ``KEEP_RECENT`` of ``window`` (the compaction point) verbatim,
    cutting at a human message so tool calls and their results stay together.
    ``force`` (the user's "compact now" on an idle agent) keeps only the final reply.
    """
    if force:
        return len(messages) - 1 if messages else 0
    budget = int(window * KEEP_RECENT)
    keep_from = len(messages)
    running = 0
    for i in range(len(messages) - 1, 0, -1):
        running += estimate_tokens([messages[i]])
        if running > budget:
            break
        if isinstance(messages[i], HumanMessage):
            keep_from = i
    if keep_from >= len(messages):  # newest human block alone exceeds budget
        keep_from = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return keep_from


def compaction_drop(messages: list[BaseMessage], point: int) -> list[BaseMessage]:
    """The messages to fold into the summary when the context passes ``point``.

    Whole earlier exchanges first. A turn long enough to pass the point on its own (a
    long tool loop) is cut inside: its opening message stays, so the transcript still
    starts with what the agent was asked, and so do its latest steps; the steps between
    go. A cut always lands on one of the agent's replies, so every tool call keeps its
    result.
    """
    keep_from = compaction_split(messages, point)
    drop = list(messages[:keep_from])
    budget = int(point * KEEP_RECENT)
    if estimate_tokens(messages[keep_from:]) <= budget:
        return drop
    cut, running = len(messages), 0
    for i in range(len(messages) - 1, keep_from, -1):
        running += estimate_tokens([messages[i]])
        if running > budget:
            break
        if isinstance(messages[i], AIMessage):
            cut = i
    # The newest step alone over budget: still drop everything before it.
    if cut >= len(messages):
        cut = max((i for i in range(keep_from + 1, len(messages))
                   if isinstance(messages[i], AIMessage)), default=len(messages))
    return drop + list(messages[keep_from + 1:cut])


async def summarize(org: Org, agent: Agent, model: Any, previous: str,
                    old: list[BaseMessage], turn_id: str | None) -> str:
    """Fold ``old`` messages into the running working-memory summary."""
    window = model.context_window
    transcript = "\n".join(
        f"{m['role'].upper()}: {m['content'][:2500]}"
        + (f" [tool calls: {', '.join(tc['name'] for tc in m.get('tool_calls', []))}]"
           if m.get("tool_calls") else "")
        for m in llm.serialize_messages(old)
    )[: int(window * 0.5 * 4)]
    prompt = [
        SystemMessage(content=(
            f"You maintain the running working-memory summary for {agent.name} "
            f"({agent.role}). Merge the previous summary with the new transcript into an updated "
            "summary written in second person ('you'). Keep: commitments and promises, open "
            "questions, decisions, facts, names, numbers, file paths, task refs (T-n), who "
            "asked for what, and what is still in progress. Drop chit-chat. Max ~600 words.")),
        HumanMessage(content=f"Previous summary:\n{previous or '(none)'}\n\n"
                             f"New transcript:\n{transcript}"),
    ]
    # A summary is a chore, not a problem to think through: without reasoning it takes a
    # few seconds instead of most of a minute.
    from dataclasses import replace

    from app.llm import effort as effort_mod

    quick = replace(model, chat=effort_mod.without_thinking(model.chat, model.provider_type),
                    effort="off")
    try:
        ai, _ = await llm.invoke(quick, prompt, org_id=org.id, agent_id=agent.id,
                                 turn_id=turn_id, purpose="compaction", stream=False)
        return llm.text_of(ai).strip() or previous
    except llm.LLMFailure as e:
        log.warning("compaction failed for %s: %s", agent.name, e)
        return (previous + "\n(Some older conversation was dropped "
                "without a summary because summarization failed.)").strip()


TRIM_AT = 0.25  # of the compaction point: past it, old bulky tool output is trimmed first
KEEP_TOOL_RESULTS = 6  # the latest results always stay whole
TRIM_BATCH = 10  # trim only once this many bulky results are older than those
BULKY = 1500  # characters; smaller results stay


def trim_old_results(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Replacements (same ids) for bulky tool results older than the latest few: a file read
    or a page of output from forty steps ago costs every later call, and the agent can read
    it again if it needs it. In batches: changing an old message makes the model server
    re-read everything after it, so trimming one result per step would cost more than it
    saves; ten at a time keeps the cached prefix intact for the steps in between."""
    results = [m for m in messages if isinstance(m, ToolMessage)]
    old = [m for m in results[:-KEEP_TOOL_RESULTS]
           if m.id and len(str(m.content)) > BULKY and not str(m.content).startswith("[trimmed:")]
    if len(old) < TRIM_BATCH:
        return []
    out = []
    for m in old:
        text = str(m.content)
        first = text.strip().splitlines()[0][:160] if text.strip() else ""
        out.append(ToolMessage(
            content=f"[trimmed: earlier {m.name or 'tool'} output, {len(text):,} characters, "
                    f"removed to save room; run it again if you need it. It began: {first}]",
            tool_call_id=m.tool_call_id, name=m.name, status=m.status, id=m.id))
    return out


async def prepare_node(state: AgentState, config: RunnableConfig) -> dict:
    messages = state.get("messages", [])
    if len(messages) < 8:
        return {}
    org, agent = await _load(config)
    model = await resolve_for_agent(agent, org)
    point = compact_point(model)
    size = context_size(state)
    if size < point * TRIM_AT:
        return {}
    if trimmed := trim_old_results(messages):
        # Cheaper than a summary and usually enough: the model call measures the new size.
        return {"messages": trimmed, "context_tokens": 0}
    if size < point:
        return {}
    old = compaction_drop(messages, point)
    if not old:
        return {}
    summary = await summarize(org, agent, model, state.get("summary", ""), old,
                              state.get("turn_id"))
    await bus.publish("agent.compacted", {"removed": len(old), "turnId": state.get("turn_id")},
                      org_id=org.id, agent_id=agent.id)
    # The last measurement covered what was just dropped: estimate until the next call.
    return {"summary": summary, "context_tokens": 0,
            "messages": [RemoveMessage(id=m.id) for m in old if m.id]}


async def model_node(state: AgentState, config: RunnableConfig) -> dict:
    org, agent = await _load(config)
    turn_id = state.get("turn_id")
    tools = await resolve_tools(org, agent)
    model = await resolve_for_agent(agent, org)
    steps = int(state.get("steps") or 0)
    max_steps = int((agent.limits or {}).get("max_steps_per_turn") or DEFAULT_MAX_STEPS)
    if steps:
        recalled, recalled_files = state.get("recalled", ""), state.get("recalled_files", "")
    else:
        recalled, recalled_files = await _recall(org, agent, state)
    messages = list(state.get("messages", []))
    # Read once a turn, so the offered tools (and the prompt cache) stay the same within it.
    sticky = state.get("sticky") if steps else await loadout.recent_tools(agent.id)
    offered, deferred = loadout.split(org, tools.tools, messages, sticky or ())
    # One system prompt per turn (and per summary): it carries the time and the live board,
    # and rebuilding it on every call changed the prompt's first bytes, so the model server
    # re-read the whole context each step instead of reusing its cache.
    key = f"{turn_id}|{hash(state.get('summary', ''))}"
    if steps and state.get("system_key") == key and state.get("system_prompt"):
        system = state["system_prompt"]
    else:
        system = await build_system_prompt(
            org, agent, summary=state.get("summary", ""), tool_warnings=tools.warnings,
            tool_names=list(tools.tools), recalled=recalled,
            recalled_files=recalled_files,
            more_tools=loadout.catalogue(tools.tools, deferred) if deferred else "",
        )
    steered: list[BaseMessage] = []
    direct = list(state.get("direct_senders") or [])
    seen: list[str] = []
    if steps:
        # The user said something while this turn was running: read it now.
        from app.agents import steering
        from app.agents.runtime import runtime

        if taken := await steering.take(agent.id, turn_id):
            content, _depth, more = await runtime._format_inbox(agent, taken)
            steered = [HumanMessage(content=f"{steering.STEER_NOTE}\n\n{content}",
                                    id=f"steer_{taken[-1].id}")]
            messages += steered
            direct = list(dict.fromkeys([*direct, *more]))
            seen = [m.id for m in taken]
            await bus.publish("agent.steered", {"turnId": turn_id, "messageIds": seen},
                              org_id=org.id, agent_id=agent.id)
    noted = dict(state.get("noted") or {})
    if noted.get("turn") != turn_id:
        noted = {"turn": turn_id}
    if steps and steps < max_steps:
        # The clock, batching, a checklist: appended, so the cached prefix holds (nudges.py).
        lines, noted = await nudges.notes(agent, messages, steps, noted)
        if lines:
            note = HumanMessage(content="[system] " + "\n".join(lines), id=f"note_{turn_id}_{steps}")
            messages.append(note)
            steered.append(note)
    stale = {m.id: m for m in prune_images(messages)}
    history = sanitize([stale.get(m.id, m) if m.id else m for m in messages])
    final = steps >= max_steps
    prompt: list[BaseMessage] = [SystemMessage(content=system), *history]
    if final:
        prompt.append(HumanMessage(content=(
            f"[system] You have used {steps} steps this turn, the limit. Stop working now: "
            "reply in plain text with what you did and what remains (no tool calls). You can "
            "continue in a later turn.")))
    await bus.publish("agent.status", {"status": "working", "detail": "thinking",
                                       "turnId": turn_id},
                      org_id=org.id, agent_id=agent.id, persist=False)
    schemas = [tools.tools[n].openai_schema() for n in offered] if not final else None
    ai, _call_id = await llm.invoke(
        model, prompt, org_id=org.id, agent_id=agent.id, turn_id=turn_id, purpose="turn",
        tools=schemas or None,
    )
    usage = ai.usage_metadata or {}
    tin, tout = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
    if turn_id:
        async with SessionLocal() as session:
            await session.execute(update(Turn).where(Turn.id == turn_id).values(
                steps=Turn.steps + 1, input_tokens=Turn.input_tokens + tin,
                output_tokens=Turn.output_tokens + tout,
                cost_usd=Turn.cost_usd + model.cost(tin, tout)))
            await session.commit()
    text = llm.text_of(ai).strip()
    if text and ai.tool_calls:
        await bus.publish("agent.thought", {"text": text[:2000], "turnId": turn_id},
                          org_id=org.id, agent_id=agent.id)
    if final and ai.tool_calls:
        ai = AIMessage(content=text or "(stopped at the step limit)", id=ai.id,
                       usage_metadata=ai.usage_metadata)
    # Tool calls must carry ids for pairing; some local servers omit them.
    for i, tc in enumerate(ai.tool_calls):
        if not tc.get("id"):
            tc["id"] = f"call_{turn_id or 'x'}_{steps}_{i}_{int(time.time() * 1000)}"
    out: dict[str, Any] = {
        "messages": [*prune_images(list(state.get("messages", []))), *steered, ai],
        "steps": steps + 1, "recalled": recalled, "recalled_files": recalled_files,
        "context_tokens": tin + tout, "system_prompt": system, "system_key": key, "noted": noted,
        "sticky": list(sticky or [])}
    if steered:
        out.update(seen=seen, direct_senders=direct)
    return out


async def _recall(org: Org, agent: Agent, state: AgentState) -> tuple[str, str]:
    """At the start of a turn: memories and file passages relevant to what just arrived."""
    from app.services import attachments, memory

    inbox = next((m for m in reversed(state.get("messages", []))
                  if isinstance(m, HumanMessage)), None)
    if inbox is None:
        return "", ""
    text = llm.text_of(inbox)
    notes = passages = ""
    try:
        found = await memory.relevant(org.id, agent.id, text)
        notes = "\n".join(f"- [{'shared' if m.agent_id is None else 'mine'} "
                          f"{m.updated_at:%Y-%m-%d}] {m.content}" for m in found)
    except Exception:  # noqa: BLE001 - memory is a help, never a blocker
        log.exception("auto-recall failed for %s", agent.name)
    try:
        hits = await attachments.relevant(org.id, agent.id, text)
        passages = "\n".join(f"- [{name}] {content[:600]}" for name, content in hits)
    except Exception:  # noqa: BLE001
        log.exception("file recall failed for %s", agent.name)
    return notes, passages


def route_after_model(state: AgentState) -> str:
    messages = state.get("messages") or []
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and last.tool_calls:
        return "gate"
    return END


async def gate_node(state: AgentState, config: RunnableConfig) -> dict:
    last = state["messages"][-1]
    assert isinstance(last, AIMessage)
    org, agent = await _load(config)
    tools = (await resolve_tools(org, agent)).tools
    decided = state.get("decisions") or {}
    needs = []
    for tc in last.tool_calls:
        t = tools.get(tc["name"])
        if t is not None and t.approval == "ask" and tc["id"] not in decided:
            needs.append({"toolCallId": tc["id"], "tool": tc["name"], "args": tc["args"],
                          "source": t.source})
    if not needs:
        return {}
    answer = interrupt({"type": "approval", "requests": needs})
    decisions = {}
    for req in needs:
        d = (answer or {}).get(req["toolCallId"]) or {"approved": False,
                                                      "note": "no decision recorded"}
        decisions[req["toolCallId"]] = {"approved": bool(d.get("approved")),
                                        "note": str(d.get("note") or "")}
    return {"decisions": decisions}


def route_after_gate(state: AgentState) -> list[Send]:
    last = state["messages"][-1]
    decisions = state.get("decisions") or {}
    calls = list(last.tool_calls)  # type: ignore[union-attr]
    ordered = [tc["id"] for tc in calls if tc["name"] in IN_ORDER]
    return [
        Send("tool", {"call": dict(tc), "decision": decisions.get(tc["id"]),
                      "turn_id": state.get("turn_id"), "depth": state.get("depth", 0),
                      "after": ordered[:ordered.index(tc["id"])] if tc["id"] in ordered else []})
        for tc in calls
    ]


# Calls that build on each other when one response makes several (a task, then one depending
# on it): they run in the order written; everything else runs at once.
IN_ORDER = frozenset({"create_task", "update_task"})
_finished: dict[str, asyncio.Event] = {}


async def _wait_for(ids: list[str]) -> None:
    for i in ids:
        await asyncio.wait_for(_finished.setdefault(i, asyncio.Event()).wait(), 120)


def _done(name: str, tc_id: str) -> None:
    if name in IN_ORDER and not _finished.setdefault(tc_id, asyncio.Event()).is_set():
        _finished[tc_id].set()
        asyncio.get_running_loop().call_later(300, _finished.pop, tc_id, None)


def _record_text(out: str) -> str:
    cap = settings.tool_record_cap_chars
    if len(out) <= cap:
        return out
    return out[:cap] + f"\n[Pantheon: record truncated, {cap:,} of {len(out):,} characters kept]"


async def _record_tool_call(tc_id: str, org_id: str, agent_id: str, turn_id: str | None, name: str,
                            args: dict[str, Any], status: str, out: str, duration: int) -> None:
    """Save the finished call. A payload the database can't store degrades the record to a
    visible placeholder; it never fails the turn (the agent already has the result)."""

    async def write(result: str, stored_args: dict[str, Any]) -> None:
        async with SessionLocal() as session:
            row = await session.get(ToolCall, tc_id)
            if row is None:
                session.add(ToolCall(id=tc_id, org_id=org_id, agent_id=agent_id, turn_id=turn_id,
                                     name=name, args=stored_args, status=status, result=result,
                                     duration_ms=duration))
            else:
                row.status, row.result, row.duration_ms = status, result, duration
                row.args = stored_args
            await session.commit()

    from app.services import secrets

    args = secrets.redact_value(org_id, args)
    try:
        await write(_record_text(out), args)
        return
    except Exception as e:  # noqa: BLE001
        detail = str(e).splitlines()[0] if str(e) else type(e).__name__
        problem = detail.split("'>: ", 1)[-1][:300]  # drop the driver's exception wrapper
        log.warning("tool call %s (%s) could not be recorded: %s", tc_id, name, problem)
    placeholder = (f"[Pantheon: this {name} result could not be stored ({problem}). "
                   f"It was {len(out):,} characters; the agent received it in full.]")
    try:
        await write(placeholder, {"_unrecorded": True, "keys": sorted(map(str, args))})
    except Exception:  # noqa: BLE001 - losing the record must not lose the turn
        log.exception("tool call %s placeholder could not be recorded either", tc_id)


async def tool_node(arg: dict[str, Any], config: RunnableConfig) -> dict:
    call = arg["call"]
    tc_id, name, args = call["id"], call["name"], call.get("args") or {}
    org, agent = await _load(config)
    turn_id = arg.get("turn_id")

    async with SessionLocal() as session:
        prior = await session.get(ToolCall, tc_id)
    if prior is not None and prior.status in ("ok", "error", "denied"):
        # Already executed (crash after execution, before the checkpoint write).
        _done(name, tc_id)
        return {"messages": [ToolMessage(content=clip(prior.result), tool_call_id=tc_id,
                                         name=name,
                                         status="success" if prior.status == "ok" else "error")]}

    tools = (await resolve_tools(org, agent)).tools
    t = tools.get(name)
    decision = arg.get("decision")
    started = time.monotonic()
    status: str
    if t is None:
        out, status = (f"ERROR: tool '{name}' is not available to you. Available tools: "
                       f"{', '.join(sorted(tools)) or 'none'}."), "error"
    elif decision is not None and not decision.get("approved"):
        note = decision.get("note") or "no reason given"
        out, status = f"DENIED: the user did not approve this call ({note}).", "denied"
    else:
        async with SessionLocal() as session:
            session.add(ToolCall(id=tc_id, org_id=org.id, agent_id=agent.id, turn_id=turn_id,
                                 name=name, args=args, status="running"))
            try:
                await session.commit()
            except Exception:  # noqa: BLE001 - row from an earlier crashed attempt
                await session.rollback()
        await bus.publish("tool.started", {"toolCallId": tc_id, "tool": name, "args": args,
                                           "source": t.source, "turnId": turn_id},
                          org_id=org.id, agent_id=agent.id)
        await bus.publish("agent.status", {"status": "working", "detail": f"using {name}",
                                           "turnId": turn_id},
                          org_id=org.id, agent_id=agent.id, persist=False)
        ctx = ToolContext(org=org, agent=agent, turn_id=turn_id, tool_call_id=tc_id,
                          depth=int(arg.get("depth") or 0))
        if name in IN_ORDER:
            try:
                await _wait_for(arg.get("after") or [])
            except TimeoutError:
                pass  # an earlier call never finished: go ahead rather than hang
        try:
            out, ok = await execute(t, args, ctx)
        finally:
            _done(name, tc_id)
        status = "ok" if ok else "error"
    _done(name, tc_id)  # also when it was denied or isn't available
    duration = int((time.monotonic() - started) * 1000)
    await _record_tool_call(tc_id, org.id, agent.id, turn_id, name, args, status, out, duration)
    await bus.publish("tool.completed", {"toolCallId": tc_id, "tool": name, "status": status,
                                         "durationMs": duration, "preview": out[:600],
                                         "turnId": turn_id},
                      org_id=org.id, agent_id=agent.id)
    text = clip(out)
    images = list(getattr(out, "images", []) or [])
    extra: list[BaseMessage] = []
    if images:
        model = await resolve_for_agent(agent, org)
        if model.vision or model.video:
            what = "Video" if any(map(is_video, images)) else "Screenshot"
            extra.append(HumanMessage(
                content=[{"type": "text", "text": f"[{what} from {name}]"},
                         *map(media_part, images)],
                additional_kwargs={"tool_image": True}, id=f"img_{tc_id}"))
        else:
            text += ("\n(A screenshot was taken, but your model isn't set up to see images. "
                     "Enable 'vision' in your model settings, or use text tools such as "
                     "browser_read.)")
    return {"messages": [ToolMessage(content=text, tool_call_id=tc_id, name=name,
                                     status="success" if status == "ok" else "error"), *extra]}


KEEP_IMAGES = 2  # screenshots are big: only the latest few stay in the context


def is_video(url: str) -> bool:
    return url.startswith("data:video/")


def media_part(url: str) -> dict[str, Any]:
    """A data: URL as a message part: video_url for a video, image_url for an image."""
    if is_video(url):
        return {"type": "video_url", "video_url": {"url": url}}
    return {"type": "image_url", "image_url": {"url": url}}


def prune_images(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Replacements (same ids) turning all but the latest screenshots into placeholders."""
    shots = [m for m in messages if isinstance(m, HumanMessage)
             and (m.additional_kwargs or {}).get("tool_image") and isinstance(m.content, list)]
    return [HumanMessage(content="[an earlier screenshot or video, removed to save context]",
                         additional_kwargs={"tool_image": True}, id=m.id)
            for m in shots[:-KEEP_IMAGES] if m.id]


def build_graph(checkpointer: Any):
    g = StateGraph(AgentState)
    g.add_node("inbox", inbox_node)
    g.add_node("prepare", prepare_node)
    g.add_node("model", model_node)
    g.add_node("gate", gate_node)
    g.add_node("tool", tool_node)
    g.add_edge(START, "inbox")
    g.add_edge("inbox", "prepare")
    g.add_edge("prepare", "model")
    g.add_conditional_edges("model", route_after_model, ["gate", END])
    g.add_conditional_edges("gate", route_after_gate, ["tool"])
    # Through prepare after every tool step, not only at the start of a turn: a long tool
    # loop grows the context until it fills the window otherwise.
    g.add_edge("tool", "prepare")
    return g.compile(checkpointer=checkpointer)
