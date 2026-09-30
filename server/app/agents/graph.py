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
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from sqlalchemy import update

from app.agents import llm
from app.agents.prompt import build_system_prompt
from app.core.config import settings
from app.core.db import SessionLocal
from app.events import bus
from app.llm.providers import resolve_for_agent
from app.models import Agent, Org, ToolCall, Turn
from app.tools.base import ToolContext
from app.tools.resolve import clip, execute, resolve_tools

log = logging.getLogger(__name__)

DEFAULT_MAX_STEPS = 30
COMPACT_AT = 0.6  # fraction of the context window
KEEP_RECENT = 0.25  # fraction of the window kept verbatim after compaction


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


def thread_config(agent_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": f"agent:{agent_id}", "agent_id": agent_id},
            "recursion_limit": 400}


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

    Automatic compaction runs once the transcript passes ``COMPACT_AT`` of the
    context window and keeps roughly ``KEEP_RECENT`` of it verbatim, cutting at
    a human message so tool calls and their results stay together. ``force``
    (the user's "compact now" on an idle agent) keeps only the final reply.
    """
    if force:
        return len(messages) - 1 if messages else 0
    budget = int(window * KEEP_RECENT)
    keep_from = len(messages)
    running = 0
    for i in range(len(messages) - 1, 0, -1):
        running += count_tokens_approximately([messages[i]])
        if running > budget:
            break
        if isinstance(messages[i], HumanMessage):
            keep_from = i
    if keep_from >= len(messages):  # newest human block alone exceeds budget
        keep_from = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage))
    return keep_from


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
    try:
        ai, _ = await llm.invoke(model, prompt, org_id=org.id, agent_id=agent.id,
                                 turn_id=turn_id, purpose="compaction", stream=False)
        return llm.text_of(ai).strip() or previous
    except llm.LLMFailure as e:
        log.warning("compaction failed for %s: %s", agent.name, e)
        return (previous + "\n(Some older conversation was dropped "
                "without a summary because summarization failed.)").strip()


async def prepare_node(state: AgentState, config: RunnableConfig) -> dict:
    messages = state.get("messages", [])
    if len(messages) < 8:
        return {}
    org, agent = await _load(config)
    model = await resolve_for_agent(agent, org)
    window = model.context_window
    if count_tokens_approximately(messages) + 4000 < window * COMPACT_AT:
        return {}
    old = messages[:compaction_split(messages, window)]
    if not old:
        return {}
    summary = await summarize(org, agent, model, state.get("summary", ""), old,
                              state.get("turn_id"))
    await bus.publish("agent.compacted", {"removed": len(old), "turnId": state.get("turn_id")},
                      org_id=org.id, agent_id=agent.id)
    return {"summary": summary,
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
    system = await build_system_prompt(
        org, agent, summary=state.get("summary", ""), tool_warnings=tools.warnings,
        tool_names=list(tools.tools), recalled=recalled,
        recalled_files=recalled_files,
    )
    messages = list(state.get("messages", []))
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
    schemas = [t.openai_schema() for t in tools.tools.values()] if not final else None
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
    return {"messages": [*prune_images(list(state.get("messages", []))), ai],
            "steps": steps + 1, "recalled": recalled,
            "recalled_files": recalled_files}


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
    return [
        Send("tool", {"call": dict(tc), "decision": decisions.get(tc["id"]),
                      "turn_id": state.get("turn_id"), "depth": state.get("depth", 0)})
        for tc in last.tool_calls  # type: ignore[union-attr]
    ]


async def tool_node(arg: dict[str, Any], config: RunnableConfig) -> dict:
    call = arg["call"]
    tc_id, name, args = call["id"], call["name"], call.get("args") or {}
    org, agent = await _load(config)
    turn_id = arg.get("turn_id")

    async with SessionLocal() as session:
        prior = await session.get(ToolCall, tc_id)
    if prior is not None and prior.status in ("ok", "error", "denied"):
        # Already executed (crash after execution, before the checkpoint write).
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
        out, ok = await execute(t, args, ctx)
        status = "ok" if ok else "error"
    duration = int((time.monotonic() - started) * 1000)
    record = out[: settings.tool_record_cap_chars]
    async with SessionLocal() as session:
        row = await session.get(ToolCall, tc_id)
        if row is None:
            session.add(ToolCall(id=tc_id, org_id=org.id, agent_id=agent.id, turn_id=turn_id,
                                 name=name, args=args, status=status, result=record,
                                 duration_ms=duration))
        else:
            row.status, row.result, row.duration_ms = status, record, duration
        await session.commit()
    await bus.publish("tool.completed", {"toolCallId": tc_id, "tool": name, "status": status,
                                         "durationMs": duration, "preview": out[:600],
                                         "turnId": turn_id},
                      org_id=org.id, agent_id=agent.id)
    text = clip(out)
    images = list(getattr(out, "images", []) or [])
    extra: list[BaseMessage] = []
    if images:
        model = await resolve_for_agent(agent, org)
        if model.vision:
            extra.append(HumanMessage(
                content=[{"type": "text", "text": f"[Screenshot from {name}]"},
                         *({"type": "image_url", "image_url": {"url": u}} for u in images)],
                additional_kwargs={"tool_image": True}, id=f"img_{tc_id}"))
        else:
            text += ("\n(A screenshot was taken, but your model isn't set up to see images. "
                     "Enable 'vision' in your model settings, or use text tools such as "
                     "browser_read.)")
    return {"messages": [ToolMessage(content=text, tool_call_id=tc_id, name=name,
                                     status="success" if status == "ok" else "error"), *extra]}


KEEP_IMAGES = 2  # screenshots are big: only the latest few stay in the context


def prune_images(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Replacements (same ids) turning all but the latest screenshots into placeholders."""
    shots = [m for m in messages if isinstance(m, HumanMessage)
             and (m.additional_kwargs or {}).get("tool_image") and isinstance(m.content, list)]
    return [HumanMessage(content="[an earlier screenshot, removed to save context]",
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
    g.add_edge("tool", "model")
    return g.compile(checkpointer=checkpointer)
